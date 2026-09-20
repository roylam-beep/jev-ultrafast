"""Local-browser freshness/execution regressions. No model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")
        # A control that paints over itself is still that control, and a click on that
        # decoration reaches the same handler a person's click would. A separate overlay is
        # not: that is exactly what a confused-deputy click lands on.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <div id="wrap" onclick="window.hits=(window.hits||0)+1"
               style="position:relative;width:200px;height:40px">
            <button id="go" style="position:absolute;inset:0">Go</button>
            <div id="deco" style="position:absolute;inset:0;background:#eee">promo</div>
          </div>
        """))
        page = browser.observe(screenshot=False)
        go = next(a for a in page["actions"] if a["label"] == "Go")
        browser.act(go, page)
        assert browser.evaluate("window.hits") == 1, "a control's own decoration blocked its click"
        passed.append("a control's own decoration does not block clicking it")

        browser.evaluate("document.querySelector('#deco').remove(); "
                         "const c=document.createElement('div'); c.id='cover'; "
                         "c.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(c)")
        page = browser.observe(screenshot=False)
        go = next(a for a in page["actions"] if a["label"] == "Go")
        try:
            browser.act(go, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Clicked through a page-covering overlay")
        assert browser.evaluate("window.hits") == 1
        passed.append("a page-covering overlay still blocks the click")

        # Typing goes wherever focus is. A click that never reached the field must not report
        # success -- the text would land somewhere else, or nowhere, and be recorded as done.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <input id="search" aria-label="Search" value="kept" style="width:300px">
        """) + "; document.querySelector('#search').addEventListener('mousedown',"
               "e=>{e.preventDefault();document.body.focus();})")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        try:
            browser.act(field, page, text="never typed")
        except StalePage:
            pass
        else:
            raise AssertionError("Reported a fill that never focused the field")
        assert browser.evaluate("document.querySelector('#search').value") == "kept"
        passed.append("a click that does not focus the field reports failure, not success")

        # Component libraries hide the real checkbox and paint a label. Both the state and the
        # clickable geometry have to come from somewhere other than the input's own box.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <label class="cb"><input id="two" type="checkbox"
             style="position:absolute;opacity:0;width:0;height:0"><span>2 rooms</span></label>
          <button id="wrapped"><input id="inner" type="checkbox" checked
             style="position:absolute;opacity:0;width:0;height:0">South district</button>
        """))
        page = browser.observe(screenshot=False)
        boxed = next((a for a in page["actions"] if a["label"].strip() == "2 rooms"), None)
        assert boxed is not None, "a label-wrapped checkbox was not indexed at all"
        assert boxed["checked"] == "false", boxed
        browser.act(boxed, page)
        assert browser.evaluate("document.querySelector('#two').checked") is True
        passed.append("a visually hidden checkbox is indexed and clicked through its label")

        wrapped = next(a for a in page["actions"] if "South district" in a["label"])
        assert wrapped["checked"] == "true", wrapped
        passed.append("a checkbox wrapped in a button reports the state the user sees")

        # A name and a price mean nothing apart. Group repeated blocks so a reader can tell
        # which lines describe one item -- and prove the grouping is stable enough to act on.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <ul id="nav"><li>Home</li><li>Deals</li><li>Help</li><li>Cart</li><li>More</li></ul>
          <div id="grid">
            <div class="card"><span class="tag">new</span><p>Widget A</p><p>1,100</p></div>
            <div class="card"><span class="tag">hot</span><p>Widget B</p><p>2,200</p></div>
            <div class="card"><span class="tag">new</span><p>Widget C</p><p>3,300</p></div>
            <div class="card"><span class="tag">new</span><p>Widget D</p><p>4,400</p></div>
          </div>
        """))
        page = browser.observe(screenshot=False)
        records = page["records"]
        named = {}
        for record in records:
            for line in record["lines"]:
                if line.startswith("Widget "):
                    named[line] = record["lines"]
        assert len(named) == 4, f"expected one record per card, got {len(named)}: {records}"
        for name, lines in named.items():
            price = {"Widget A": "1,100", "Widget B": "2,200", "Widget C": "3,300", "Widget D": "4,400"}[name]
            assert price in lines, f"{name} lost its price: {lines}"
        passed.append("a name and its price land in the same record")

        # The one-word tag is a label, not a record; it must fold into the card it decorates.
        assert all("new" not in r["lines"] or any(x.startswith("Widget") for x in r["lines"])
                   for r in records), records
        assert any("new" in lines for lines in named.values()), named
        passed.append("a one-line container folds into the record around it")

        marked = [ln for ln in page["text"].split("\n") if ln.startswith("\u27e6")]
        assert not any(ln in ("Home", "Deals", "Help") for r in records for ln in r["lines"]), records
        passed.append("a class-less repeated list does not become records")

        # The flat text and the structured field must be the same grouping, or a reader that
        # trusts one and a reader that trusts the other will disagree.
        rebuilt, node_id = {}, None
        for line in page["text"].split("\n"):
            if line.startswith("\u27e6") and line.endswith("\u27e7"):
                inner = line[1:-1]
                node_id = int(inner) if inner else None
                continue
            if node_id is not None:
                rebuilt.setdefault(node_id, []).append(line)
        assert rebuilt == {r["node"]: r["lines"] for r in records}, (rebuilt, records)
        assert marked, "no record markers were emitted into the text"
        assert len(page["text"]) <= 6000
        passed.append("the markers in the text rebuild exactly the records field")

        # The ids ride inside the freshness marker, so unstable numbering would turn every
        # decision into a StalePage. Same DOM, read twice.
        again = browser.observe(screenshot=False)
        assert [r["node"] for r in again["records"]] == [r["node"] for r in records], (again["records"], records)
        assert browser.fresh(page), "a second read of an unchanged page invalidated the first"
        passed.append("record ids survive a second observation of the same page")

        # One text node can hold its own line breaks (a pre-wrap description). Those must not
        # become record lines, or the flat text and the records field describe different
        # groupings -- and a line of page text could impersonate a record marker.
        browser.evaluate("document.body.innerHTML=" + repr("""
          <div id="jobs">
            <div class="job"><p>Role One</p><pre>duty a
duty b

\u27e6999\u27e7</pre><p>Salary 100</p></div>
            <div class="job"><p>Role Two</p><pre>duty c
duty d</pre><p>Salary 200</p></div>
            <div class="job"><p>Role Three</p><pre>duty e
duty f</pre><p>Salary 300</p></div>
            <div class="job"><p>Role Four</p><pre>duty g
duty h</pre><p>Salary 400</p></div>
          </div>
        """))
        page = browser.observe(screenshot=False)
        assert not any("\n" in line for r in page["records"] for line in r["lines"]), page["records"]
        rebuilt, node_id = {}, None
        for line in page["text"].split("\n"):
            if line.startswith("\u27e6") and line.endswith("\u27e7") and line[1:-1].isdigit():
                node_id = int(line[1:-1])
                continue
            if line == "\u27e6\u27e7":
                node_id = None
                continue
            if node_id is not None:
                rebuilt.setdefault(node_id, []).append(line)
        assert rebuilt == {r["node"]: r["lines"] for r in page["records"]}, (rebuilt, page["records"])
        roles = {ln for r in page["records"] for ln in r["lines"] if ln.startswith("Role ")}
        assert len(roles) == 4, roles
        passed.append("a text node's own line breaks do not split or fake a record")


        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
