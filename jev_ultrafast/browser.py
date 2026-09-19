"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

from .keyboard import key_events
from .pointer import scroll_expression

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

# Action kinds whose mutation happens inside a Runtime.evaluate, so an interrupted
# evaluation is an uncertain execution rather than a stale read.
MUTATING_EVALUATIONS = {"select": "Dropdown", "scroll": "Scroll"}

# A WAIT costs a decision, so it is worth more than a fixed slice; it still has to
# return well inside the loop's budget when the page never settles.
WAIT_BUDGET = 2.0
WAIT_IDLE_MS = 250
# snapshot.js guard() puts the surrounding scope's text last. Everything before it is the
# element's own identity and state; that prefix is what survives a page that animates itself.
GUARD_SCOPE_TEXT = 13
# Long enough for a marquee, ticker or ad rotation to advance between two reads of the guard.
VOLATILE_PROBE_S = 0.12


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class UnreachableTarget(StalePage):
    """The page is current, but this element cannot be operated: covered, gone, or disabled.

    Distinct from a plain StalePage, where the decision simply aged out and the same target may
    still be the right one. Re-observing cannot fix an unreachable target, so the agent records
    the refusal and lets the policy pick a different route instead of retrying the same element.
    """


class Browser:
    def __init__(self, url):
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        # Events only arrive while the domain is on. Enabling once here keeps every
        # later wait at zero extra protocol calls.
        self.network_enabled = False
        self.traffic = None
        try:
            self.call("Network.enable")
            self.network_enabled = True
            # Subscribed for the session, not for the wait: any consumer's pump moves the
            # daemon's whole buffer, so a recording run's screencast thread would otherwise
            # discard the requests that started before the WAIT decision was even made.
            # waits imports StalePage from here, so the import is local.
            from .waits import network_subscription

            self.traffic = network_subscription(self.session)
        except RuntimeError:
            pass  # A bridge without the Network domain still runs; waits fall back to time.
        try:
            # Page.navigate returns once the navigation commits, so readyState already
            # describes the new document. No document-identity check is needed here.
            self.call("Page.navigate", url=url)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.evaluate("document.readyState") == "complete":
                    break
                time.sleep(0.02)
        except Exception:
            self.close()  # A session that never opened still owns a tab and a subscription.
            raise

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        # Element-scoped where an element is named. The whole-page marker is the right check
        # for "is this observation still usable", but the wrong one for "is this target still
        # the same": a carousel or an ad slot rewrites the marker every second and would veto
        # every action on a page that is merely alive. The guard compares the target itself.
        if action is not None and action["kind"] in {"click", "select", "fill"}:
            node = action["node"]
            if type(node) is not int:
                return False
            expected = [page["page_key"], page["guards"].get(str(node))]
            current = self._guard(node)
            if current == expected:
                return True
            # The guard disagrees, which normally means the target is not what was observed.
            # But a control that animates its own surroundings -- a placeholder marquee, a
            # "N people viewing" ticker -- rewrites the scope text on a timer, and would then
            # veto every action on itself forever. Read it again: if it moved with no input
            # from us, the scope text carries no signal here, so fall back to the element's
            # own identity and state, which an actual substitution still changes.
            time.sleep(VOLATILE_PROBE_S)
            again = self._guard(node)
            if again != current:
                return _identity_only(again) == _identity_only(expected)
            return False
        return self.evaluate(MARKER) == page["marker"]

    def _guard(self, node):
        return self.evaluate(
            "(() => { const c=window.__jevFast; "
            f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
        )

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            # WAIT means the page is working. Return the moment it goes quiet instead
            # of sleeping a fixed slice and spending another decision to look again.
            from .waits import wait_for_network_idle

            wait_for_network_idle(self, timeout=WAIT_BUDGET, idle_ms=WAIT_IDLE_MS)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.traffic:
            self.traffic.close()
            self.traffic = None
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def _identity_only(pair):
    """A guard with the scope text dropped: identity, role, name, value and state only."""
    if not isinstance(pair, list) or len(pair) != 2:
        return None
    key, guard = pair
    return [key, guard[:GUARD_SCOPE_TEXT] if isinstance(guard, list) else guard]


def progress_key(state):
    """What an action could have changed, with the page's own animation left out.

    fingerprint() answers "is this observation still current" and must notice everything,
    text included. Progress is a different question: on a page carrying a carousel, a ticker
    or an ad slot, the text is never the same twice, so a fingerprint comparison reports
    progress after every single action and the no-progress stop never fires. Compare the
    things an action moves -- where we are, how far we have scrolled, and which controls
    exist -- and a page that is merely alive stops looking like a page that is advancing.
    """
    controls = sorted({f"{a['kind']}:{a['label']}" for a in state["actions"]})
    content = {"url": state["url"], "scroll": state["scroll"], "controls": controls}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def fingerprint(state):
    # Geometry is resolved and hit-tested just before input, so it carries no meaning here.
    # Including rect would report page_changed for every animation frame and defeat the
    # no-progress stop that hands a stuck run to the supervisor.
    semantics = [{k: v for k, v in action.items() if k != "rect"} for action in state["actions"]]
    content = {"url": state["url"], "text": state["text"], "actions": semantics, "scroll": state["scroll"]}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            # These kinds mutate inside the evaluation itself, so an interruption may
            # land after the page already changed. Reporting it as staleness would let
            # the loop re-predict and mutate twice, with the first never logged.
            kind = request["action"]["kind"] if operation == "act" else None
            if kind in MUTATING_EVALUATIONS:
                raise RuntimeError(f"{MUTATING_EVALUATIONS[kind]} execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            # CDP drops mouseWheel on a background target, and the agent owns one.
            evaluate(scroll_expression(action["delta"]))
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]')) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              // Same box the snapshot indexed, and the same visibility test: a hidden checkbox
              // is seen and clicked through its label. Judging the input's own box would reject
              // every control a component library paints for itself.
              const box=window.__jevFast.box ? window.__jevFast.box(e) : e;
              if (!box.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              const r=box.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              const hit=document.elementFromPoint(x,y);
              // A control may paint its own decoration over itself -- a placeholder marquee, a
              // custom caret, an icon layer. That is still this control, and the click reaches it.
              // A separate overlay is not: it is what a confused-deputy click would land on. The
              // three conditions below keep the second case blocked.
              if (!e.contains(hit) && !box.contains(hit)) {
                if (!hit) return null;
                const interactive='a[href],button,input,textarea,select,summary,[contenteditable="true"],'+
                  '[role="button"],[role="link"],[role="checkbox"],[role="radio"],[role="switch"],'+
                  '[role="tab"],[role="menuitem"],[role="option"],[role="combobox"],[role="textbox"]';
                // 1. Whatever is on top must not be a control of its own, or it owns the click.
                if (hit.closest(interactive) && hit.closest(interactive)!==e) return null;
                // 2. It must share a container with the target that is not the page itself.
                const chain=new Set(); for (let n=e; n; n=n.parentElement) chain.add(n);
                let common=null; for (let n=hit; n; n=n.parentElement) if (chain.has(n)) { common=n; break; }
                if (!common || common===document.body || common===document.documentElement) return null;
                // 3. It must be about the size of the target. A page-covering layer never is.
                const hr=hit.getBoundingClientRect();
                if (hr.width > r.width*1.5 || hr.height > r.height*1.5) return null;
              }
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise UnreachableTarget("Target is covered, disabled, or gone. Choose another route.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    # Typing is the one operation whose success is checkable before it happens:
                    # insertText goes to whatever holds focus. If the click did not land on the
                    # field -- a decoration swallowed it, the page moved focus elsewhere -- the
                    # text would be typed into some other element, or nowhere, and reported as
                    # done. Verify the outcome instead of trusting the hit test's prediction.
                    focused = evaluate(
                        "(() => { const e=window.__jevFast?.nodes.get(" + str(action["node"]) + ");"
                        " return !!e && document.activeElement===e; })()"
                    )
                    if not focused:
                        raise UnreachableTarget("Clicking the field did not focus it; nothing was typed.")
                    # Replace, do not append. A page can intercept the accelerator, so
                    # the selectAll command is what makes the selection actually happen.
                    for event in key_events("ControlOrMeta+a"):
                        call("Input.dispatchKeyEvent", **event)
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    info["progress_key"] = progress_key(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
