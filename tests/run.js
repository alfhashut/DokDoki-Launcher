import { cases } from "./helpers.js";
import "./worker.test.js";
import "./website.test.js";
import "./turnstile.test.js";

window.DOKDOKI_TEST_RESULT = { done: false, passed: 0, failed: 0, failures: [] };
for (const { name, run } of cases) {
  const item = document.createElement("li");
  try {
    await run();
    item.textContent = `PASS: ${name}`;
    window.DOKDOKI_TEST_RESULT.passed += 1;
  } catch (error) {
    item.textContent = `FAIL: ${name}: ${error.message}`;
    window.DOKDOKI_TEST_RESULT.failed += 1;
    window.DOKDOKI_TEST_RESULT.failures.push(item.textContent);
  }
  document.getElementById("results").append(item);
  document.getElementById("summary").textContent = `${window.DOKDOKI_TEST_RESULT.passed} passed, ${window.DOKDOKI_TEST_RESULT.failed} failed`;
}
window.DOKDOKI_TEST_RESULT.done = true;
