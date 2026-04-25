export interface ElectionAlgorithmBootstrap {
  runbookUrl: string;
  verifyBallotHashUrl: string;
  verifyBallotChainUrl: string;
  verifyAuditLogUrl: string;
}

export function readElectionAlgorithmBootstrap(root: HTMLElement): ElectionAlgorithmBootstrap | null {
  const runbookUrl = String(root.dataset.electionAlgorithmRunbookUrl || "").trim();
  const verifyBallotHashUrl = String(root.dataset.electionAlgorithmVerifyBallotHashUrl || "").trim();
  const verifyBallotChainUrl = String(root.dataset.electionAlgorithmVerifyBallotChainUrl || "").trim();
  const verifyAuditLogUrl = String(root.dataset.electionAlgorithmVerifyAuditLogUrl || "").trim();

  if (!runbookUrl || !verifyBallotHashUrl || !verifyBallotChainUrl || !verifyAuditLogUrl) {
    return null;
  }

  return {
    runbookUrl,
    verifyBallotHashUrl,
    verifyBallotChainUrl,
    verifyAuditLogUrl,
  };
}