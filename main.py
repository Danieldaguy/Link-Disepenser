import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
import random
import json
import aiohttp
from dotenv import load_dotenv
from collections import Counter
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# --- Minimal HTTP server to pass Koyeb health check ---
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_server():
    server = HTTPServer(("0.0.0.0", 8000), Handler)
    server.serve_forever()

Thread(target=run_server, daemon=True).start()
# --------------------------------------------------------

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

LINK_FILE = 'links.json'
USAGE_FILE = 'usage.json'
STATS_FILE = 'stats.json'
RESET_INTERVAL = timedelta(weeks=1)

# Use actual role IDs
ROLE_LINK_LIMITS = {
    1302679552572657784: 3,  # "verified" role ID
    1302679532909625456: 20,  # "🔥♨️Burning 🔥" role ID
    130267955257: 20,  # "w booster" role ID
}

def load_json(path, default):
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(default, f)
    with open(path, 'r') as f:
        return json.load(f)

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)

LINKS = load_json(LINK_FILE, [])
user_link_usage = load_json(USAGE_FILE, {})
# Initialize stats file with basic structure
link_stats = load_json(STATS_FILE, {
    "total_links_sent": 0,
    "links_by_day": {},
    "links_by_hour": {},
    "popular_links": {},
    "active_users": {}
})

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    reset_usage.start()
    await tree.sync()
    print("Slash commands synced.")

@tasks.loop(hours=24)
async def reset_usage():
    global user_link_usage
    now = datetime.utcnow()
    changed = False

    for user_id in list(user_link_usage.keys()):
        last_time = datetime.strptime(user_link_usage[user_id]["last_link_time"], "%Y-%m-%d %H:%M:%S.%f")
        if now - last_time > RESET_INTERVAL:
            del user_link_usage[user_id]
            changed = True
            print(f"Reset link usage for user ID: {user_id}")

    if changed:
        save_json(USAGE_FILE, user_link_usage)

def normalize_link(link: str):
    link = link.strip()
    if not link.startswith("http://") and not link.startswith("https://"):
        link = "https://" + link
    return link

async def check_valid_url(url: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                return response.status < 400
    except:
        return False

# Update the statistics for link usage
def update_link_stats(user_id, link):
    now = datetime.utcnow()
    today = now.strftime("%Y-%m-%d")
    hour = now.strftime("%H")
    
    # Update total links count
    link_stats["total_links_sent"] += 1
    
    # Update links by day
    if today not in link_stats["links_by_day"]:
        link_stats["links_by_day"][today] = 0
    link_stats["links_by_day"][today] += 1
    
    # Update links by hour
    if hour not in link_stats["links_by_hour"]:
        link_stats["links_by_hour"][hour] = 0
    link_stats["links_by_hour"][hour] += 1
    
    # Update popular links
    if link not in link_stats["popular_links"]:
        link_stats["popular_links"][link] = 0
    link_stats["popular_links"][link] += 1
    
    # Update active users
    if user_id not in link_stats["active_users"]:
        link_stats["active_users"][user_id] = 0
    link_stats["active_users"][user_id] += 1
    
    # Save updated stats
    save_json(STATS_FILE, link_stats)

async def send_random_link(user):
    try:
        random_link = random.choice(LINKS)
        normalized_link = normalize_link(random_link)
        
        # Get remaining links info
        remaining = get_user_remaining_links(user)
        remaining_text = "unlimited links remaining (admin)" if remaining == "unlimited" else f"{remaining} links remaining this week"
        
        await user.send(f"Here is your random link: {normalized_link}\n\n🔗 You have {remaining_text}.")
        
        # Update statistics
        update_link_stats(str(user.id), normalized_link)
        
    except discord.Forbidden:
        await user.send("⚠️ I couldn't send you a DM. Please enable DMs from server members.")
    except Exception as e:
        print(f"Error sending link: {e}")

def get_user_remaining_links(user):
    user_id_str = str(user.id)
    
    if user.guild_permissions.administrator:
        return "unlimited"  # Admins have unlimited links
    
    user_role_ids = [role.id for role in user.roles]
    applicable_limits = [limit for role_id, limit in ROLE_LINK_LIMITS.items() if role_id in user_role_ids]
    
    if not applicable_limits:
        return 0  # No eligible roles
    
    link_limit = max(applicable_limits)
    
    if user_id_str not in user_link_usage:
        return link_limit  # Haven't used any links yet
    
    used_links = user_link_usage[user_id_str]["links_sent"]
    remaining = max(0, link_limit - used_links)
    
    return remaining

@tree.command(name="mylinks", description="Check how many links you have left this week")
async def mylinks(interaction: discord.Interaction):
    user = interaction.user
    remaining = get_user_remaining_links(user)
    
    if remaining == "unlimited":
        await interaction.response.send_message("🔗 You have unlimited links available (admin).", ephemeral=True)
    else:
        await interaction.response.send_message(f"🔗 You have {remaining} links remaining this week.", ephemeral=True)

@tree.command(name="showlink", description="Admin only: Show all available links in the database")
@app_commands.checks.has_permissions(administrator=True)
async def showlink(interaction: discord.Interaction):
    if not LINKS:
        await interaction.response.send_message("⚠️ No links in the database yet.", ephemeral=True)
        return
    
    # Create embed for links
    embed = discord.Embed(
        title="🔗 Available Links",
        description=f"There are {len(LINKS)} links in the database:",
        color=discord.Color.green()
    )
    
    # Split links into chunks if there are too many
    chunks = [LINKS[i:i+10] for i in range(0, len(LINKS), 10)]
    
    for i, chunk in enumerate(chunks):
        links_text = "\n".join([f"{i*10+idx+1}. {link}" for idx, link in enumerate(chunk)])
        embed.add_field(
            name=f"Links {i*10+1}-{i*10+len(chunk)}" if i > 0 else "Links",
            value=links_text,
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="showusage", description="Admin only: Show link dispenser usage statistics")
@app_commands.checks.has_permissions(administrator=True)
async def showusage(interaction: discord.Interaction):
    guild = interaction.guild
    
    embed = discord.Embed(
        title="📊 Link Dispenser Statistics",
        description=f"Total links sent: **{link_stats['total_links_sent']}**",
        color=discord.Color.gold()
    )
    
    # Most active day
    if link_stats["links_by_day"]:
        most_active_day = max(link_stats["links_by_day"].items(), key=lambda x: x[1])
        embed.add_field(
            name="Most Active Day",
            value=f"{most_active_day[0]}: {most_active_day[1]} links",
            inline=True
        )
    
    # Most active hour
    if link_stats["links_by_hour"]:
        most_active_hour = max(link_stats["links_by_hour"].items(), key=lambda x: x[1])
        embed.add_field(
            name="Most Active Hour",
            value=f"{most_active_hour[0]}:00: {most_active_hour[1]} links",
            inline=True
        )
    
    # Most popular links (top 5)
    if link_stats["popular_links"]:
        popular_links = sorted(link_stats["popular_links"].items(), key=lambda x: x[1], reverse=True)[:5]
        popular_text = "\n".join([f"{idx+1}. {link[:30]}... ({count} times)" for idx, (link, count) in enumerate(popular_links)])
        embed.add_field(
            name="Most Popular Links",
            value=popular_text or "No data yet",
            inline=False
        )
    
    # Most active users (top 5)
    if link_stats["active_users"]:
        active_users = sorted(link_stats["active_users"].items(), key=lambda x: x[1], reverse=True)[:5]
        active_text = ""
        for idx, (user_id, count) in enumerate(active_users):
            try:
                member = await guild.fetch_member(int(user_id))
                username = member.display_name
            except:
                username = f"Unknown User ({user_id})"
            active_text += f"{idx+1}. {username}: {count} links\n"
        
        embed.add_field(
            name="Most Active Users",
            value=active_text or "No data yet",
            inline=False
        )
    
    # Current week usage summary
    total_used_this_week = sum(data["links_sent"] for data in user_link_usage.values())
    active_users_this_week = len(user_link_usage)
    
    embed.add_field(
        name="Current Week Summary",
        value=f"• Links used this week: {total_used_this_week}\n• Active users this week: {active_users_this_week}",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="resetlinks", description="Admin only: Reset link counts for a user or the whole server")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    user="The user to reset links for (leave empty to reset for all users)",
    confirm="Type 'confirm' to reset all users' links (required for server-wide reset)"
)
async def resetlinks(interaction: discord.Interaction, user: discord.User = None, confirm: str = None):
    global user_link_usage
    
    if user:
        # Reset for specific user
        user_id_str = str(user.id)
        if user_id_str in user_link_usage:
            user_link_usage[user_id_str]["links_sent"] = 0
            user_link_usage[user_id_str]["last_link_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
            save_json(USAGE_FILE, user_link_usage)
            await interaction.response.send_message(f"✅ Reset link count for user {user.display_name}.", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ User {user.display_name} has no link usage history.", ephemeral=True)
    else:
        # Reset for all users
        if confirm != "confirm":
            await interaction.response.send_message("⚠️ To reset links for ALL users, you must include 'confirm' parameter.", ephemeral=True)
            return
            
        user_link_usage = {}
        save_json(USAGE_FILE, user_link_usage)
        await interaction.response.send_message("✅ Reset link counts for all users.", ephemeral=True)

@tree.command(name="showlinks", description="Admin only: Show link usage for all users")
@app_commands.checks.has_permissions(administrator=True)
async def showlinks(interaction: discord.Interaction):
    if not user_link_usage:
        await interaction.response.send_message("ℹ️ No link usage data available.", ephemeral=True)
        return
    
    guild = interaction.guild
    
    # Build the links list
    usage_info = []
    for user_id, data in user_link_usage.items():
        try:
            member = await guild.fetch_member(int(user_id))
            username = member.display_name
        except:
            username = f"Unknown User ({user_id})"
        
        links_sent = data["links_sent"]
        last_time = datetime.strptime(data["last_link_time"], "%Y-%m-%d %H:%M:%S.%f")
        time_diff = datetime.utcnow() - last_time
        days_ago = time_diff.days
        
        # Get user's link limit
        if member:
            user_role_ids = [role.id for role in member.roles]
            applicable_limits = [limit for role_id, limit in ROLE_LINK_LIMITS.items() if role_id in user_role_ids]
            link_limit = max(applicable_limits) if applicable_limits else 0
            remaining = max(0, link_limit - links_sent)
        else:
            remaining = "Unknown"
        
        usage_info.append(f"{username}: {links_sent} used, {remaining} remaining (last used {days_ago} days ago)")
    
    # Create embed with user information
    embed = discord.Embed(
        title="🔗 Link Usage Information",
        description="Here's the current link usage for all users:",
        color=discord.Color.blue()
    )
    
    # Split into chunks if too long
    chunks = [usage_info[i:i+25] for i in range(0, len(usage_info), 25)]
    
    for i, chunk in enumerate(chunks):
        embed.add_field(
            name=f"Users {i*25+1}-{i*25+len(chunk)}" if i > 0 else "Users",
            value="\n".join(chunk),
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="dispenser", description="Send the link dispenser button.")
@app_commands.checks.has_permissions(administrator=True)
async def dispenser(interaction: discord.Interaction):
    embed = discord.Embed(
    title="🔗 Link Dispenser",
    description=(
        "## Click the button below to receive a random link\n"
        "- Verified✅ = 3\n"
        "- Burning🔥 = ~~10~~ 20 **EVENT DEAL!!**\n"
        "- Booster⭐ = 20"
    ),
    color=discord.Color.blue()
)

    button = discord.ui.Button(label="Get Random Link", style=discord.ButtonStyle.green, custom_id="get_random_link")
    view = discord.ui.View()
    view.add_item(button)

    try:
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.defer(ephemeral=True, thinking=False)
    except:
        await interaction.response.send_message("Couldn't send the dispenser message.", ephemeral=True)

@tree.command(name="addlink", description="Admin only: Add one or more new links.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(links="Paste one or more links separated by commas, spaces, or newlines")
async def addlink(interaction: discord.Interaction, links: str):
    await interaction.response.defer(ephemeral=True)  # prevents 'Unknown interaction' error

    # Split links by commas, newlines, or multiple spaces
    raw_links = [l.strip() for l in links.replace(",", " ").replace("\n", " ").split()]
    new_links = [normalize_link(link) for link in raw_links]
    added = []

    for link in new_links:
        if link not in LINKS and await check_valid_url(link):
            LINKS.append(link)
            added.append(link)

    save_json(LINK_FILE, LINKS)

    if added:
        await interaction.followup.send(f"✅ Added {len(added)} link(s):\n" + "\n".join(added), ephemeral=True)
    else:
        await interaction.followup.send("⚠️ No valid new links added.", ephemeral=True)

@tree.command(name="removelink", description="Admin only: Remove one or more links.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(links="Paste one or more links separated by commas, spaces, or newlines")
async def removelink(interaction: discord.Interaction, links: str):
    raw_links = [l.strip() for l in links.replace(",", " ").replace("\n", " ").split()]
    to_remove = [normalize_link(link) for link in raw_links]
    removed = [link for link in to_remove if link in LINKS]

    for link in removed:
        LINKS.remove(link)

    save_json(LINK_FILE, LINKS)

    if removed:
        await interaction.response.send_message(f"✅ Removed {len(removed)} link(s):\n" + "\n".join(removed), ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ No matching links found to remove.", ephemeral=True)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component and interaction.data.get("custom_id") == "get_random_link":
        await handle_get_link(interaction)

async def handle_get_link(interaction: discord.Interaction):
    user = interaction.user
    guild = interaction.guild

    if not guild:
        await interaction.response.send_message("This command can only be used within a server.", ephemeral=True)
        return

    if user.guild_permissions.administrator:
        await send_random_link(user)
        await interaction.response.send_message("🔗 Check your DMs for your link!", ephemeral=True)
        return

    user_role_ids = [role.id for role in user.roles]
    applicable_limits = [limit for role_id, limit in ROLE_LINK_LIMITS.items() if role_id in user_role_ids]

    if not applicable_limits:
        await interaction.response.send_message("❌ You don't have a role that allows you to receive links.", ephemeral=True)
        return

    link_limit = max(applicable_limits)
    user_id_str = str(user.id)

    if user_id_str not in user_link_usage:
        user_link_usage[user_id_str] = {
            "links_sent": 0,
            "last_link_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        }

    if user_link_usage[user_id_str]["links_sent"] >= link_limit:
        await interaction.response.send_message("⚠️ You have already received the maximum number of links for this week.", ephemeral=True)
    else:
        await send_random_link(user)
        user_link_usage[user_id_str]["links_sent"] += 1
        user_link_usage[user_id_str]["last_link_time"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
        save_json(USAGE_FILE, user_link_usage)
        await interaction.response.send_message("🔗 Check your DMs for your link!", ephemeral=True)

bot.run(TOKEN)
