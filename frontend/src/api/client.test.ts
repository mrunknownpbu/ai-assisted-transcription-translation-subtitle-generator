import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API_KEY_STORAGE, ApiError, api } from "./client";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const headerOf = (call: unknown[]) =>
  ((call[1] as RequestInit).headers as Record<string, string>)["X-API-Key"];

describe("API key handling", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let promptMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    promptMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("prompt", promptMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends no key and never prompts when the server needs none", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { deleted: "j1" }));
    await api.deleteJob("j1");
    expect(headerOf(fetchMock.mock.calls[0])).toBeUndefined();
    expect(promptMock).not.toHaveBeenCalled();
  });

  it("asks once on 401, retries with the key, and remembers it", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: "missing or invalid X-API-Key" }))
      .mockResolvedValueOnce(json(200, { deleted: "j1" }))
      .mockResolvedValueOnce(json(200, { deleted: "j2" }));
    promptMock.mockReturnValue(" secret ");

    await expect(api.deleteJob("j1")).resolves.toEqual({ deleted: "j1" });
    expect(headerOf(fetchMock.mock.calls[1])).toBe("secret");
    expect(localStorage.getItem(API_KEY_STORAGE)).toBe("secret");

    await api.deleteJob("j2");
    expect(headerOf(fetchMock.mock.calls[2])).toBe("secret");
    expect(promptMock).toHaveBeenCalledTimes(1);
  });

  it("keeps JSON content type alongside the key", async () => {
    localStorage.setItem(API_KEY_STORAGE, "k");
    fetchMock.mockResolvedValueOnce(json(200, { job: {} }));
    await api.retryJob("j1");
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers).toEqual({ "Content-Type": "application/json", "X-API-Key": "k" });
  });

  it("surfaces the 401 and stores nothing when the prompt is cancelled", async () => {
    fetchMock.mockResolvedValueOnce(json(401, { detail: "missing or invalid X-API-Key" }));
    promptMock.mockReturnValue(null);
    await expect(api.cancelJob("j1")).rejects.toThrow(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(API_KEY_STORAGE)).toBeNull();
  });

  it("forgets a wrong key so the next action asks again", async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: "bad" }))
      .mockResolvedValueOnce(json(401, { detail: "missing or invalid X-API-Key" }));
    promptMock.mockReturnValue("wrong");
    await expect(api.cancelJob("j1")).rejects.toThrow("missing or invalid X-API-Key");
    expect(localStorage.getItem(API_KEY_STORAGE)).toBeNull();
  });

  it("uploads send the key without forcing a JSON content type", async () => {
    localStorage.setItem(API_KEY_STORAGE, "k");
    fetchMock.mockResolvedValueOnce(json(201, { upload_id: "u" }));
    await api.uploadSrt(new File(["1"], "a.srt"));
    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers).toEqual({ "X-API-Key": "k" });
  });
});
