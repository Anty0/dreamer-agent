// Function-hooks API is early access; names here are from claude-code.d.ts of the
// bundled CLI. A hook that throws is passed through, so a rename degrades to the
// prompt framing in prompts/*.md rather than breaking the run.
const DROP = new Set(["environment", "model", "date"]);

export function register(on) {
  on("prompt.context", ($, e, next) => next({ ...e, blocks: [] }));
  on("prompt.attachment", ($, e, next) => (DROP.has(e.type) ? { text: null } : next(e)));
}
