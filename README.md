# 🏈 Gridiron GM — College Football Manager

A college football general-management simulation game. Single-file web app — no build, no backend, no dependencies.

**Play it:** open `index.html` in any browser, or visit the GitHub Pages URL for this repo.

## Features

- **32 fictional programs** across 4 conferences — pick a powerhouse or rebuild a bottom-feeder
- **Coach mode**: drive-by-drive play-calling for every game, including the playoffs, with fourth-down and two-point decisions at the key moments
- **Full playbooks**: 5 offensive × 4 defensive schemes with matchup dynamics and weekly gameplans
- **Coaching staff market**: hire and lose coordinators (OC / DC / Recruiting / S&C) on a prestige-based salary pool
- **Recruiting**: weekly interest battles against AI programs, recruit personalities, official visits, Signing Day, transfer portal with NIL points
- **Roster management**: player development, morale, injuries, redshirts, Sorare-style collectible player cards, clickable player files with full career history
- **Rivalries**: a cross-conference rival every year on the final week, with your job security on the line
- **Legacy**: program and national record books, a Hall of Fame for your greatest players, and 14 career achievements
- **Career mode**: board confidence, hot seat, firings, job offers from bigger programs
- **Season loop**: 11-game regular season → conference championships → 4-team playoff → awards (POY, DPOY, Coach of the Year)
- **Big moments**: weekly "around the league" recap with poll movement, Signing Day card reveal, title ceremonies, and a summer camp development report

Progress is saved automatically in your browser (`localStorage`). Existing careers carry over across updates.

## Development

Everything lives in `index.html` (vanilla JS/CSS). To run locally:

```bash
python3 -m http.server 4173
# open http://localhost:4173
```

---

🤖 Built with [Claude Code](https://claude.com/claude-code)
