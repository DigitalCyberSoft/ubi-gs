"""Rated results into the ladder: Elo ratings and the 42 ladder fields (#49).

At the end of a rated match every client submits, for every human player, 22 values from the game's
`NWorld::CPlayersStatsAccumulator` (filled at 0x0085d260, read with 0x00d62ba0):

  0       result: 1 won, 0 lost (defeated or surrendered), 2 removed/disconnected, -1 unset
  1       race: 0 Haven, 1 Preserve, 2 Academy, 3 Dungeon, 4 Necropolis, 5 Inferno
  2..7    creature recruitment value per creature faction (no ladder field uses it)
  8..13   heroes per hero race
  14      heroes lost
  15      enemy heroes defeated
  16      average hero level, 16.16 fixed point
  17      time played, seconds
  18..21  always 0

The Profile screen shows TOT_TIME_PLAYED / 60 and AVERAGE_HERO_LEVEL / 65536 (0x009f6950), so the ladder keeps
seconds and 16.16 fixed point too. The game computes no rating itself.
"""
import os, sys
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(root_dir)
import accounts, ladder

LADDER_ID = '1'
"""The ladder the game queries in every channel; rooms carry event id 7, so the room can't name it."""

K = 32
"""Elo K-factor."""

VALUES = 22
RESULT_LOST, RESULT_WON, RESULT_DISCONNECTED = 0, 1, 2
IDX_RESULT, IDX_RACE, IDX_HEROES, IDX_HEROES_LOST, IDX_DEFEATED, IDX_AVG_LEVEL, IDX_TIME = 0, 1, 8, 14, 15, 16, 17

MAX_TIME = 7 * 24 * 3600
"""One match's time played is capped (a week), against nonsense values."""

MAX_COUNT = 1000
"""Hero counts are capped too."""

RACES = ladder.RACES[:6]
"""The races 1.60 has (no Dwarves)."""

def ladder_key(game: str):
  return ladder.ladder_key(game, LADDER_ID)

def parse(values):
  """The 22 values as ints, or None."""
  if not isinstance(values, list) or len(values) < VALUES:
    return None
  try:
    return [int(v) for v in values[:VALUES]]
  except (TypeError, ValueError):
    return None

def won(v):
  return v[IDX_RESULT] == RESULT_WON

def lost(v):
  return v[IDX_RESULT] in (RESULT_LOST, RESULT_DISCONNECTED)

def apply(outcomes: list, stored: dict):
  """New ladder fields of the players, [(account id, values)], from their stored ones {id: {field: value}}."""
  stats = {aid: ladder.defaults() | stored.get(aid, {}) for aid, _ in outcomes}
  # Elo, pairwise between a winner and a loser
  delta = {aid: 0.0 for aid, _ in outcomes}
  n = max(1, len(outcomes) - 1)
  for i, (a, va) in enumerate(outcomes):
    for b, vb in outcomes[i + 1:]:
      if not (won(va) and lost(vb) or lost(va) and won(vb)):
        continue
      expected = 1 / (1 + 10 ** ((stats[b]['RATING'] - stats[a]['RATING']) / 400))
      change = K * ((1.0 if won(va) else 0.0) - expected) / n
      delta[a] += change
      delta[b] -= change
  result = {}
  for aid, v in outcomes:
    s = stats[aid]
    s['RATING'] = max(0, s['RATING'] + round(delta[aid]))
    s['GAMES_PLAYED'] += 1
    race = RACES[v[IDX_RACE]] if 0 <= v[IDX_RACE] < len(RACES) else None
    if won(v):
      s['WINS'] += 1
      s['CUR_WINS_STREAK'] += 1
      s['CUR_LOSSES_STREAK'] = 0
      s['MAX_WINS_STREAK'] = max(s['MAX_WINS_STREAK'], s['CUR_WINS_STREAK'])
      if race:
        s[f'W_{race}'] += 1
    elif lost(v):
      s['LOSSES'] += 1
      s['CUR_LOSSES_STREAK'] += 1
      s['CUR_WINS_STREAK'] = 0
      s['MAX_LOSSES_STREAK'] = max(s['MAX_LOSSES_STREAK'], s['CUR_LOSSES_STREAK'])
      if race:
        s[f'L_{race}'] += 1
    if v[IDX_RESULT] == RESULT_DISCONNECTED:
      s['DISCONNECTIONS'] += 1
    if race:
      s[f'G_{race}'] += 1
    s['TOT_TIME_PLAYED'] += min(max(v[IDX_TIME], 0), MAX_TIME)
    for r, name in enumerate(RACES):
      heroes = min(max(v[IDX_HEROES + r], 0), MAX_COUNT)
      s['TOT_HEROES_HIRED'] += heroes
      s[f'H_{name}'] += heroes
    s['TOT_HEROES_LOST'] += min(max(v[IDX_HEROES_LOST], 0), MAX_COUNT)
    s['TOT_HEROES_DEFEATED'] += min(max(v[IDX_DEFEATED], 0), MAX_COUNT)
    # a running mean over the games played, in 16.16 fixed point
    level = min(max(v[IDX_AVG_LEVEL], 0), 100 << 16)
    s['AVERAGE_HERO_LEVEL'] = (s['AVERAGE_HERO_LEVEL'] * (s['GAMES_PLAYED'] - 1) + level) // s['GAMES_PLAYED']
    result[aid] = s
  return result

def match_finished(match: dict):
  """Applies a finished match: {'game', 'players' (account names), 'accounts' {name: Account},
  'results' {name: values}, 'disputed' {names}}. Returns the accounts rated."""
  players = {p.lower() for p in match['players']}
  outcomes, seen = [], set()
  for name, values in match['results'].items():
    v = parse(values)
    account = match['accounts'].get(name)
    if v is None:
      print(f"Match results for {name} unreadable, not rated")
      continue
    if account is None or account.name.lower() not in players or account.id in seen:
      print(f"Match results for {name}, who isn't one of its players, not rated")
      continue
    if name in match['disputed']:
      print(f"The players' results for {name} disagree, not rated")
      continue
    seen.add(account.id)
    outcomes.append((account, v))
  if not outcomes:
    return []
  key = ladder_key(match['game'])
  accounts.update_ladder(key, [a.id for a, _ in outcomes], lambda stored: apply([(a.id, v) for a, v in outcomes], stored))
  for account, v in outcomes:
    print(f"{account.name}: result {v[IDX_RESULT]}, ladder {key} updated")
  return [a for a, _ in outcomes]
