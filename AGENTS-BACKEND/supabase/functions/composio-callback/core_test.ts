import { assertEquals } from "jsr:@std/assert@1";
import { callbackState, sha256Hex } from "./core.ts";

Deno.test("accepts a sufficiently long callback state", () => {
  assertEquals(callbackState(new Request("https://example.test/callback?state=" + "a".repeat(32))), "a".repeat(32));
});

Deno.test("rejects a short state", () => {
  assertEquals(callbackState(new Request("https://example.test/callback?state=short")), null);
});

Deno.test("hash is stable", async () => {
  assertEquals(await sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});
