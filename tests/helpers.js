export const cases = [];
export function test(name, run) { cases.push({ name, run }); }
export function assert(value, message = "Assertion failed") { if (!value) throw new Error(message); }
export function equal(actual, expected, message = "Values differ") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`${message}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`);
}
export async function until(predicate, message = "Condition timed out") {
  const deadline = Date.now() + 4000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  throw new Error(message);
}
