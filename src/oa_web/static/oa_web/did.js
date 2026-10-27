// "Have you done this?" for buttons that only RECORD something done outside
// the system (guide.Button.did): the button's question in a dialog, with
// "don't ask again for this step" remembered per browser (localStorage).
// Without <dialog> support or with storage blocked, the buttons still work.
(function () {
  var PREFIX = "oa-did-skip:";
  var dlg = document.getElementById("did-dialog");
  var text = document.getElementById("did-text");
  var skip = document.getElementById("did-skip");
  var reset = document.getElementById("did-reset");
  var pending = null;

  function get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function set(key) { try { localStorage.setItem(key, "1"); } catch (e) { /* ignore */ } }
  function skippedKeys() {
    var keys = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf(PREFIX) === 0) keys.push(k);
      }
    } catch (e) { /* ignore */ }
    return keys;
  }

  if (reset && skippedKeys().length) {
    reset.hidden = false;
    reset.querySelector("a").addEventListener("click", function (ev) {
      ev.preventDefault();
      skippedKeys().forEach(function (k) { try { localStorage.removeItem(k); } catch (e) {} });
      reset.hidden = true;
    });
  }
  if (!dlg || typeof dlg.showModal !== "function") return;

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("button[data-did]") : null;
    if (!b || b.disabled) return;
    if (b.dataset.didOk === "1") { b.dataset.didOk = ""; return; }   // confirmed: submit
    if (get(PREFIX + b.dataset.didKey) === "1") return;
    if (b.form && !b.form.checkValidity()) return;   // the browser shows what is missing
    ev.preventDefault();
    pending = b;
    text.textContent = b.dataset.did;
    skip.checked = false;
    dlg.showModal();
  });

  // The dialog's own buttons, handled directly (not via its "close" event,
  // which some browsers fire late or not at all). Esc just closes it.
  dlg.querySelector('button[value="yes"]').addEventListener("click", function (ev) {
    ev.preventDefault();
    var b = pending;
    pending = null;
    dlg.close("yes");
    if (!b) return;
    if (skip.checked) set(PREFIX + b.dataset.didKey);
    b.dataset.didOk = "1";
    b.click();   // submits with this button's name/value
  });
  dlg.querySelector('button[value="no"]').addEventListener("click", function (ev) {
    ev.preventDefault();
    pending = null;
    dlg.close("no");
  });
})();
