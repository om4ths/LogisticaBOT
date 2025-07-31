import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import json
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv(
    "GUILD_ID")  # Não usado diretamente no código, mas mantido para referência

if not TOKEN:
    print("❌ ERRO: TOKEN não encontrado nas variáveis de ambiente!")
    print("Por favor, adicione seu token do Discord bot nas Secrets.")
    exit(1)

print("Token configurado:", "✅" if TOKEN else "❌")

CANAL_ID_HOSPEDAGEM = int("1386760046456868925") # ID do canal onde o status é exibido
CANAL_ID_LOGS = int("1386793302623391814") # ID do canal para logs

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

conexoes = {}

class MenuSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="BR Data APP 01"),
            discord.SelectOption(label="BR Data App 02"),
            discord.SelectOption(label="BR Data - Banco de Dados (SQL/Mongo)"),
            discord.SelectOption(label="BR Data - Hospedagem Compartilhada"),
            discord.SelectOption(label="Soul - App 01"),
            discord.SelectOption(label="Soul - BD SQL"),
            discord.SelectOption(label="Soul - BD MongoDB"),
        ]
        super().__init__(placeholder="Selecione um recurso...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        recurso = self.values[0]
        user = interaction.user

        if recurso in conexoes:
            await interaction.response.send_message(f"❌ {recurso} já está em uso por {conexoes[recurso]['user'].mention}", ephemeral=True)
            return

        conexoes[recurso] = {"user": user, "thread": None}

        canal = bot.get_channel(HOSPEDAGEM_CHANNEL_ID)
        log = bot.get_channel(LOG_CHANNEL_ID)

        thread = await canal.create_thread(name=f"{user.display_name} - {recurso}", type=discord.ChannelType.public_thread)
        conexoes[recurso]["thread"] = thread

        await log.send(f"✅ {user.mention} iniciou uso de **{recurso}**")

        await interaction.response.send_message(f"🔓 Você iniciou o uso de **{recurso}**", ephemeral=True)
        atualizar_mensagem_menu.start()

        bot.loop.create_task(encerrar_uso_automatico(recurso, user))

class MenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MenuSelect())

@bot.event
async def on_ready():
    print(f"🤖 Bot conectado como {bot.user}")
    await bot.tree.sync(guild=discord.Object(id=GUILD_ID))

    canal = bot.get_channel(HOSPEDAGEM_CHANNEL_ID)
    try:
        with open("dados.json", "r") as f:
            data = json.load(f)
            mensagem = await canal.fetch_message(data["message_id"])
    except Exception:
        mensagem = await canal.send("Selecione um recurso:", view=MenuView())
        with open("dados.json", "w") as f:
            json.dump({"message_id": mensagem.id}, f)
    print("✅ Menu pronto.")

@tree.command(name="encerraruso", description="Encerra seu uso atual", guild=discord.Object(id=GUILD_ID))
async def encerraruso(interaction: discord.Interaction):
    user = interaction.user
    for recurso, info in list(conexoes.items()):
        if info["user"].id == user.id:
            thread = info["thread"]
            await thread.send(f"🔒 {user.mention} encerrou o uso de **{recurso}**.")
            del conexoes[recurso]
            canal = bot.get_channel(LOG_CHANNEL_ID)
            await canal.send(f"🛑 {user.mention} encerrou o uso de **{recurso}**")
            await interaction.response.send_message(f"✅ Você encerrou o uso de **{recurso}**", ephemeral=True)
            atualizar_mensagem_menu.start()
            return
    await interaction.response.send_message("❌ Você não está usando nenhum recurso.", ephemeral=True)

async def encerrar_uso_automatico(recurso, user):
    await discord.utils.sleep_until(discord.utils.utcnow() + discord.timedelta(seconds=TEMPO_MAXIMO))
    if recurso in conexoes and conexoes[recurso]["user"].id == user.id:
        thread = conexoes[recurso]["thread"]
        await thread.send(f"⏰ Tempo esgotado. {user.mention} foi desconectado de **{recurso}**.")
        del conexoes[recurso]
        canal = bot.get_channel(LOG_CHANNEL_ID)
        await canal.send(f"⏱️ {user.mention} foi desconectado automaticamente de **{recurso}**")
        atualizar_mensagem_menu.start()

@tasks.loop(seconds=5)
async def atualizar_mensagem_menu():
    try:
        canal = bot.get_channel(HOSPEDAGEM_CHANNEL_ID)
        with open("dados.json", "r") as f:
            data = json.load(f)
        mensagem = await canal.fetch_message(data["message_id"])
        texto = "**Status dos Recursos:**\n"
        for option in MenuSelect().options:
            status = conexoes.get(option.label)
            if status:
                texto += f"🔴 {option.label} - {status['user'].display_name}\n"
            else:
                texto += f"🟢 {option.label} - Livre\n"
        await mensagem.edit(content=texto, view=MenuView())
    except Exception as e:
        print("Erro ao atualizar mensagem:", e)
    atualizar_mensagem_menu.stop()

bot.run(TOKEN)