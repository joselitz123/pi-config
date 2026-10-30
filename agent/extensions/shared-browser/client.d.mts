export interface BrowserContent {
  type: "text" | "image";
  text?: string;
  data?: string;
  mimeType?: string;
}
export interface BrowserResult {
  owner?: string;
  pageId?: number;
  generation?: string;
  pid?: number;
  mcp?: { content: BrowserContent[]; isError?: boolean };
  pages?: { id: number; url: string; title?: string; owner?: string | null }[];
  [key: string]: unknown;
}
export interface BrowserOptions {
  stateDir?: string;
  command?: string[];
  signal?: AbortSignal;
}
export function browserRequest(owner: string, operation: Record<string, unknown>, options?: BrowserOptions): Promise<BrowserResult>;
export function defaultStateDir(): string;
export class BrowserRequestError extends Error {
  code: string;
  sent: boolean;
}
