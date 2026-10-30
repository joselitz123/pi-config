// Deterministic local OpenAI-compatible stream: exercises Pi's real tool pipeline,
// including nested calls, without contacting or paying for an AI model.
import assert from "node:assert/strict";
import { createServer } from "node:http";

export async function toolModel() {
  const turns = [];
  let sequence = 0;
  const server = createServer(async (request, response) => {
    for await (const _chunk of request) { /* consume request */ }
    const turn = turns.shift();
    if (!turn) { response.writeHead(500).end("Unexpected model request"); return; }
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    const chunk = (delta, finish_reason = null) => response.write(`data: ${JSON.stringify({
      id: "local-browser-test", object: "chat.completion.chunk", created: 0, model: "browser-test",
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`);
    if (turn.tool) {
      chunk({ role: "assistant", tool_calls: [{ index: 0, id: turn.id, type: "function",
        function: { name: turn.tool, arguments: JSON.stringify(turn.args) } }] });
      chunk({}, "tool_calls");
    } else {
      chunk({ role: "assistant", content: "done" });
      chunk({}, "stop");
    }
    response.end("data: [DONE]\n\n");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return {
    model: {
      id: "browser-test", name: "browser-test", api: "openai-completions", provider: "browser-test",
      baseUrl: `http://127.0.0.1:${server.address().port}/v1`, reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 1000,
    },
    async call(session, tool, args) {
      const id = `call_test_${++sequence}`;
      turns.push({ id, tool, args }, {});
      await session.prompt("Execute the controlled local tool test.");
      const result = session.messages.find((message) => message.role === "toolResult" && message.toolCallId === id);
      assert.ok(result, `Pi must execute the model-issued ${tool} call: ${JSON.stringify(session.messages.slice(-2))}`);
      return result;
    },
    async close() { await new Promise((resolve) => server.close(resolve)); },
  };
}
