// Načíta frontend/app.js mimo prehliadača (len s tým, čo skript potrebuje pri načítaní)
// a overí, že submitOnce nepustí druhé odoslanie formulára, kým prvé beží.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const context = { t: (sk) => sk, document: { addEventListener() {} }, matchMedia: () => ({ matches: false }), console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "frontend", "app.js"), "utf8"), context);

function fakeForm() {
  const button = { disabled: false };
  const form = {
    button,
    addEventListener(type, handler) {
      assert.strictEqual(type, "submit");
      form.submit = () => handler({ preventDefault() { form.prevented += 1; } });
    },
    querySelector: (selector) => (selector === "[type=submit]" ? button : null),
    prevented: 0,
  };
  return form;
}

(async () => {
  // druhé odoslanie počas čakania sa ignoruje a tlačidlo je zablokované
  let calls = 0;
  let release;
  const form = fakeForm();
  context.submitOnce(form, () => {
    calls += 1;
    return new Promise((resolve) => { release = resolve; });
  });
  const first = form.submit();
  await form.submit();
  assert.strictEqual(calls, 1);
  assert.strictEqual(form.button.disabled, true);
  assert.strictEqual(form.prevented, 2);   // ani ignorované odoslanie nesmie stránku načítať znova

  // po dokončení sa dá odoslať znova
  release();
  await first;
  assert.strictEqual(form.button.disabled, false);
  const second = form.submit();
  assert.strictEqual(calls, 2);
  release();
  await second;

  // akcia, ktorá zlyhá, nenechá formulár zablokovaný
  const failing = fakeForm();
  context.submitOnce(failing, async () => { throw new Error("sieť"); });
  await assert.rejects(failing.submit(), /sieť/);
  assert.strictEqual(failing.button.disabled, false);
  let recovered = 0;
  const again = fakeForm();
  context.submitOnce(again, async () => { recovered += 1; });
  await again.submit();
  await again.submit();
  assert.strictEqual(recovered, 2);

  console.log("ok");
})();
