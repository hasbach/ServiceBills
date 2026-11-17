/**
 * Ask why a financial record is being deleted (or a day reopened). The reason
 * is mandatory and is stored with the deletion in Finance > History.
 * Returns the trimmed reason, or null when the user cancels.
 */
export function askDeleteReason(question) {
    let message = `${question}\n\nReason (required, kept in History):`;
    for (;;) {
        const answer = window.prompt(message);
        if (answer === null) return null;
        const reason = answer.trim();
        if (reason) return reason.slice(0, 300);
        message = `A reason is required.\n\n${question}\n\nReason (kept in History):`;
    }
}
