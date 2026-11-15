import { defineEval } from "@grok/agent-sdk/evals";

export default defineEval({
  tags: ["smoke"],
  description: "NL play path: search Spotify then queue_play (eval-mocked tools).",
  async test(t) {
    await t.send(
      'Play the Spotify track "Neon Pulse" by Eval Artist on the MuSync hub.',
    );
    t.succeeded();
    t.calledTool("search_streaming");
    t.calledTool("queue_play");
  },
});
