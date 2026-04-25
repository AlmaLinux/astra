export interface DebugSankeyFlow {
  from: string;
  to: string;
  flow: number;
}

export interface DebugSankeyBootstrap {
  flowsJsonId: string;
  electedJsonId: string;
  eliminatedJsonId: string;
}

export function readDebugSankeyBootstrap(root: HTMLElement): DebugSankeyBootstrap | null {
  const flowsJsonId = String(root.dataset.debugSankeyFlowsId || "").trim();
  const electedJsonId = String(root.dataset.debugSankeyElectedId || "").trim();
  const eliminatedJsonId = String(root.dataset.debugSankeyEliminatedId || "").trim();
  if (!flowsJsonId || !electedJsonId || !eliminatedJsonId) {
    return null;
  }
  return { flowsJsonId, electedJsonId, eliminatedJsonId };
}

export function readJsonScript<T>(id: string, fallback: T): T {
  const element = document.getElementById(id);
  if (!element?.textContent) {
    return fallback;
  }
  try {
    return JSON.parse(element.textContent) as T;
  } catch {
    return fallback;
  }
}