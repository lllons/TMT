// The wire contract with the Python backend, as types. The prose is
// docs/tui-protocol.md; if the two disagree, fix one of them.
//
// Events come off a pipe, so every field is read defensively by the code that
// uses it: these types say what the backend sends, not what can be trusted.

export interface Caption {
  clock?: string;
  provider?: string;
  model?: string;
}

export interface Meter {
  tokens_in?: number;
  tokens_out?: number;
  tokens_out_exact?: boolean;
  lines_added?: number;
  lines_removed?: number;
  agent_tokens?: number;
  turn_tokens?: number;
  elapsed?: number;
}

export interface Choice {
  key: string;
  label: string;
}

export interface PlanStep {
  id?: string;
  position?: number;
  title?: string;
  status?: string;
}

export interface Check {
  name?: string;
  status?: string;
}

export interface AgentCard {
  id?: string;
  number?: number;
  kind?: string;
  task?: string;
  status?: string;
  activity?: string;
}

export interface Settings {
  protocol?: string;
  mouse?: string;
  effort?: string;
  provider?: string;
  provider_label?: string;
  model?: string;
  model_label?: string;
  configured?: boolean;
  version?: string;
}

export type BackendEvent =
  | ({ type: 'ready'; workspace?: string; columns?: number; rows?: number } & Settings)
  | ({ type: 'settings' } & Settings)
  | { type: 'session'; workspace?: string; tip?: { gesture?: string; detail?: string } }
  | { type: 'prompt'; placeholder?: string; caption?: Caption; meter?: Meter }
  | { type: 'task'; text?: string; caption?: Caption }
  | { type: 'command'; title?: string; ok?: boolean; rows?: { label?: string; value?: string; text?: string }[];
      note?: string; prompt_for?: string; to_menu?: boolean }
  | { type: 'turn_start'; task?: string }
  | { type: 'status'; state?: string; label?: string; progress?: number | null }
  | ({ type: 'meter' } & Meter)
  | { type: 'delta'; text?: string }
  | { type: 'reply_reset' }
  | { type: 'event'; kind?: string; message?: string; stage?: string; level?: number; detail?: Record<string, unknown> }
  | { type: 'tool_start' | 'tool_end'; id?: number }
  | { type: 'approval'; id: number; question?: string; hint?: string; choices?: Choice[]; pattern?: string; action?: string }
  | { type: 'question'; id: number; text?: string; keys?: string[]; question?: string | null; options?: string[] | null }
  | { type: 'plan'; steps?: PlanStep[] }
  | { type: 'review'; state?: string; headline?: string; cycles?: number; required?: boolean }
  | { type: 'verify'; state?: string; headline?: string; checks?: Check[]; cycles?: number; required?: boolean }
  | { type: 'agents'; agents?: AgentCard[] }
  | { type: 'notice'; level?: string; text?: string }
  | { type: 'text'; text?: string }
  | { type: 'final'; text?: string }
  | { type: 'turn_end'; task?: string; answer?: string; outcome?: string; suggestion?: string }
  | { type: 'protocol_error'; message?: string; id?: number; line?: string }
  | { type: 'exit'; code?: number; reason?: string; message?: string };

/** What the front end sends. `v: 1` is added on the way out. */
export type FrontendMessage =
  | { type: 'hello' | 'resize'; columns: number; rows: number }
  | { type: 'submit'; text: string }
  | { type: 'approval_reply'; id: number; answer: string }
  | { type: 'answer'; id: number; key: string | null }
  | { type: 'interrupt' | 'reload' | 'quit' };
