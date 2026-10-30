// Request counters with daily and monthly ceilings. When a ceiling is reached the agent stops
// answering (and stops spending on connector calls) until the window rolls over.

export function createUsageTracker({ dailyLimit, monthlyLimit }, clock = () => new Date()) {
  const counts = { day: '', dayCount: 0, month: '', monthCount: 0 };

  function roll() {
    const now = clock().toISOString();
    const day = now.slice(0, 10);
    const month = now.slice(0, 7);
    if (counts.day !== day) Object.assign(counts, { day, dayCount: 0 });
    if (counts.month !== month) Object.assign(counts, { month, monthCount: 0 });
  }

  return {
    tryConsume() {
      roll();
      if (counts.dayCount >= dailyLimit) return { ok: false, reason: `daily limit of ${dailyLimit} requests reached` };
      if (counts.monthCount >= monthlyLimit) return { ok: false, reason: `monthly limit of ${monthlyLimit} requests reached` };
      counts.dayCount += 1;
      counts.monthCount += 1;
      return { ok: true };
    },
    snapshot() {
      roll();
      return { today: counts.dayCount, dailyLimit, thisMonth: counts.monthCount, monthlyLimit };
    }
  };
}
