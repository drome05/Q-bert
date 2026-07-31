-- Core user row, one per member who has ever interacted with the bot
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Per-server settings, editable via /settings instead of editing .env.
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id TEXT PRIMARY KEY,
    currency_name TEXT NOT NULL DEFAULT 'Pink Slips',
    currency_emoji TEXT NOT NULL DEFAULT '🏁',
    valorant_updates_channel_id TEXT,
    inhouse_staff_role_id TEXT,
    inhouse_voice_category_id TEXT,
    twitch_announcements_channel_id TEXT
);

-- Economy -- keyed per-guild: the same Discord user has an independent
-- balance in every server the bot is in.
CREATE TABLE IF NOT EXISTS economy (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    balance INTEGER NOT NULL DEFAULT 0,
    last_daily TIMESTAMP,
    last_weekly TIMESTAMP,
    last_monthly TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS economy_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    user_id TEXT REFERENCES users(user_id),
    amount INTEGER NOT NULL,          -- positive or negative
    reason TEXT NOT NULL,             -- 'daily', 'weekly', 'monthly', 'inhouse_win', 'inhouse_loss', 'valorant_rankup', 'blackjack', 'coinflip', 'slots', 'inhouse_predict', 'admin_adjust', 'give'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Valorant linkage -- per-guild: the same Discord user can link a
-- different Riot ID in each server.
CREATE TABLE IF NOT EXISTS valorant_accounts (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    riot_name TEXT NOT NULL,
    riot_tag TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'na',
    last_known_rank TEXT,             -- e.g. "Gold 2"
    last_known_rr INTEGER,            -- rank rating within tier, for detecting sub-tier rank-ups
    last_checked TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- Inhouses: MMR -- per-guild ladder.
CREATE TABLE IF NOT EXISTS inhouse_mmr (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    mmr INTEGER NOT NULL DEFAULT 1000,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- Inhouses: queue (ephemeral but persisted in case of restart) -- per-guild,
-- so two servers running inhouses at once don't drain each other's queues.
CREATE TABLE IF NOT EXISTS inhouse_queue (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

-- Inhouses: matches -- guild_id is a plain column (match_id stays the PK)
-- so match-scoped child tables/routes need no changes at all; only the
-- handful of list/filter-by-guild routes (queue, leaderboard, history,
-- stats, active-match) read it.
CREATE TABLE IF NOT EXISTS inhouse_matches (
    match_id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    captain_a TEXT REFERENCES users(user_id),
    captain_b TEXT REFERENCES users(user_id),
    draft_method TEXT NOT NULL,       -- 'snake', 'random', 'balanced_mmr'
    status TEXT NOT NULL DEFAULT 'drafting',  -- 'drafting', 'in_progress', 'voting', 'disputed', 'completed', 'cancelled'
    winning_team TEXT,                -- 'A' or 'B', null until reported
    reported_by TEXT,
    text_channel_id TEXT,
    voice_channel_a_id TEXT,
    voice_channel_b_id TEXT,
    map TEXT
);

CREATE TABLE IF NOT EXISTS inhouse_match_players (
    match_id INTEGER REFERENCES inhouse_matches(match_id),
    user_id TEXT REFERENCES users(user_id),
    team TEXT NOT NULL,               -- 'A' or 'B'
    mmr_before INTEGER,
    mmr_after INTEGER,
    PRIMARY KEY (match_id, user_id)
);

-- Inhouses: per-player result votes (NeatQueue-style)
CREATE TABLE IF NOT EXISTS inhouse_match_votes (
    match_id INTEGER REFERENCES inhouse_matches(match_id),
    user_id TEXT REFERENCES users(user_id),
    voted_team TEXT NOT NULL,         -- the team this player says WON: 'A' or 'B'
    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (match_id, user_id)
);

-- Inhouses: substitutions made mid-match
CREATE TABLE IF NOT EXISTS inhouse_substitutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES inhouse_matches(match_id),
    player_out TEXT REFERENCES users(user_id),
    player_in TEXT REFERENCES users(user_id),
    subbed_by TEXT REFERENCES users(user_id),
    subbed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Inhouses: spectator predictions/betting on match outcome
CREATE TABLE IF NOT EXISTS inhouse_predictions (
    match_id INTEGER REFERENCES inhouse_matches(match_id),
    user_id TEXT REFERENCES users(user_id),
    predicted_team TEXT NOT NULL,     -- 'A' or 'B'
    amount INTEGER NOT NULL,
    settled INTEGER NOT NULL DEFAULT 0,  -- 0/1 boolean
    payout INTEGER,                   -- filled in once settled
    placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (match_id, user_id)
);

-- Twitch linkage -- per-guild: same reasoning as valorant_accounts.
CREATE TABLE IF NOT EXISTS twitch_accounts (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    twitch_username TEXT NOT NULL,
    is_live INTEGER NOT NULL DEFAULT 0,
    last_stream_id TEXT,               -- dedup key: only announce on a genuinely new stream
    last_checked TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);
