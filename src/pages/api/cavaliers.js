const CAVS_TEAM_ID = 1610612739;
const GAME_STATUS_FINAL = 3;
const SCHEDULE_URLS = [
  "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
  "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
];

function teamDisplayName(slug) {
  return slug === "cavaliers" ? "cavs" : slug;
}

export async function GET() {
  try {
    let allCavsGames = [];

    for (const url of SCHEDULE_URLS) {
      const response = await fetch(url);
      if (!response.ok) continue;

      const data = await response.json();
      const gameDates = data.leagueSchedule?.gameDates ?? [];

      for (const day of gameDates) {
        for (const game of day.games ?? []) {
          const isCavsGame =
            game.homeTeam?.teamId === CAVS_TEAM_ID ||
            game.awayTeam?.teamId === CAVS_TEAM_ID;
          if (isCavsGame && game.gameStatus === GAME_STATUS_FINAL) {
            allCavsGames.push(game);
          }
        }
      }

      if (allCavsGames.length > 0) break;
    }

    allCavsGames.sort(
      (a, b) =>
        new Date(b.gameDateTimeUTC).getTime() -
        new Date(a.gameDateTimeUTC).getTime()
    );
    const latestGame = allCavsGames[0];

    if (!latestGame) {
      return jsonResponse({ fallback: true }, 200);
    }

    const away = latestGame.awayTeam;
    const home = latestGame.homeTeam;
    const awayName = teamDisplayName(away.teamSlug).toLowerCase();
    const homeName = teamDisplayName(home.teamSlug).toLowerCase();
    const cavsWon =
      (home.teamId === CAVS_TEAM_ID && home.score > away.score) ||
      (away.teamId === CAVS_TEAM_ID && away.score > home.score);
    const result = cavsWon ? "w!" : "oof";
    const prefix = `${awayName} @ ${homeName} (${away.score} – ${home.score}) `;

    return jsonResponse({ prefix, result }, 200);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error("Cavaliers API error:", msg);
    return jsonResponse({ fallback: true }, 200);
  }
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "public, s-maxage=43200, stale-while-revalidate=86400",
    },
  });
}
