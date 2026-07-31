import discord


def balance_embed(user: discord.abc.User, balance: int, currency_name: str, currency_emoji: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{currency_emoji} Balance",
        description=f"**{user.display_name}** has **{balance:,} {currency_name}**",
        color=discord.Color.gold(),
    )
    return embed


def economy_leaderboard_embed(rows: list[tuple[str, int]], guild: discord.Guild, currency_name: str, currency_emoji: str) -> discord.Embed:
    embed = discord.Embed(title=f"{currency_emoji} {currency_name} Leaderboard", color=discord.Color.gold())
    if not rows:
        embed.description = "No one has any coins yet."
        return embed
    lines = []
    for i, (user_id, balance) in enumerate(rows, start=1):
        member = guild.get_member(int(user_id))
        name = member.display_name if member else f"<@{user_id}>"
        lines.append(f"**{i}.** {name} — {balance:,}")
    embed.description = "\n".join(lines)
    return embed


def inhouse_leaderboard_embed(rows: list[tuple], guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(title="🏆 Inhouse Leaderboard", color=discord.Color.blurple())
    if not rows:
        embed.description = "No ranked matches played yet."
        return embed
    lines = []
    for i, row in enumerate(rows, start=1):
        user_id, mmr, wins, losses = row["user_id"], row["mmr"], row["wins"], row["losses"]
        member = guild.get_member(int(user_id))
        name = member.display_name if member else f"<@{user_id}>"
        total = wins + losses
        winrate = (wins / total * 100) if total else 0.0
        lines.append(f"**{i}.** {name} — {mmr} MMR ({wins}W/{losses}L, {winrate:.0f}%)")
    embed.description = "\n".join(lines)
    return embed


def match_embed(match_id: int, team_a: list[str], team_b: list[str], status: str) -> discord.Embed:
    embed = discord.Embed(title=f"Inhouse Match #{match_id}", color=discord.Color.blurple())
    embed.add_field(name="Team A", value="\n".join(f"<@{u}>" for u in team_a) or "—", inline=True)
    embed.add_field(name="Team B", value="\n".join(f"<@{u}>" for u in team_b) or "—", inline=True)
    embed.set_footer(text=f"Status: {status}")
    return embed


def result_embed(match_id: int, winning_team: str, votes_a: int, votes_b: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"Inhouse Match #{match_id} — Team {winning_team} Wins!",
        description=f"Final vote: Team A {votes_a} — Team B {votes_b}",
        color=discord.Color.green(),
    )
    return embed


def error_embed(message: str) -> discord.Embed:
    return discord.Embed(description=f"⚠️ {message}", color=discord.Color.red())
