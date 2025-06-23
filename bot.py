import asyncio
import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

print ("o bot está funcionando")
print("Token:", os.getenv("TOKEN"))

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
CANAL_ID_HOSPEDAGEM = int("1386760995627602103")
CANAL_ID_LOGS = int("1386763639373041675")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

recursos = {
    "BR Data APP 01": None,
    "BR Data App 02": None,
    "BR Data - Banco de Dados (SQL/Mongo)": None,
    "BR Data - Hospedagem Compartilhada": None,
    "Soul - App 01": None,
    "Soul - BD SQL": None,
    "Soul - BD MongoDB": None
}

timers = {}

class MenuConexao(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="Selecione o servidor para se conectar",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=nome, description="Clique para conectar")
            for nome in recursos
        ]
    )
    async def select_callback(self, select, interaction):
        recurso = select.values[0]
        usuario = interaction.user

        if recursos[recurso] is None:
            recursos[recurso] = usuario
            await interaction.response.send_message(f"🔌 Você se conectou ao **{recurso}**.", ephemeral=True)
            await logar(f"{usuario.mention} conectou ao **{recurso}**")
            iniciar_timer(recurso)
        elif recursos[recurso] == usuario:
            recursos[recurso] = None
            await interaction.response.send_message(f"❌ Você se desconectou do **{recurso}**.", ephemeral=True)
            await logar(f"{usuario.mention} desconectou do **{recurso}**")
            cancelar_timer(recurso)
        else:
            # This means recursos[recurso] is not None and not the current user
            await interaction.response.send_message(f"🚫 O **{recurso}** já está em uso por {recursos[recurso].mention}.", ephemeral=True)


        await atualizar_status()

async def atualizar_status():
    canal = bot.get_channel(CANAL_ID_HOSPEDAGEM)
    msg_id = await buscar_msg_fixa(canal)
    conteudo = "**💻 Status das Conexões:**\n\n"

    for nome, usuario in recursos.items():
        if usuario:
            conteudo += f"{nome}: 🔴 Em uso por {usuario.mention}\n"
        else:
            conteudo += f"{nome}: ✅ Disponível\n"

    view = MenuConexao()

    if msg_id:
        msg = await canal.fetch_message(msg_id)
        await msg.edit(content=conteudo, view=view)
    else:
        nova_msg = await canal.send(content=conteudo, view=view)
        await nova_msg.pin()

async def buscar_msg_fixa(canal):
    pins = await canal.pins()
    for msg in pins:
        if msg.author == bot.user:
            return msg.id
    return None

async def logar(mensagem):
    canal = bot.get_channel(CANAL_ID_LOGS)
    await canal.send(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")

def iniciar_timer(recurso):
    async def desconectar():
        await asyncio.sleep(14400)  # 4 horas = 14400 segundos
        if recursos[recurso]:
            await logar(f"⏱ Tempo expirado: {recursos[recurso].mention} foi desconectado de **{recurso}**.")
            recursos[recurso] = None
            await atualizar_status()
    timers[recurso] = asyncio.create_task(desconectar())

def cancelar_timer(recurso):
    if recurso in timers and timers[recurso]:
        timers[recurso].cancel()
        timers[recurso] = None

@bot.tree.command(name="iniciaruso")
@app_commands.describe(recurso="Nome do recurso para se conectar")
async def iniciaruso(interaction: discord.Interaction, recurso: str):
    if recurso not in recursos:
        await interaction.response.send_message("❌ Esse recurso não existe.", ephemeral=True)
        return
    if recursos[recurso] is not None:
        await interaction.response.send_message("🚫 Esse recurso já está em uso.", ephemeral=True)
        return
    recursos[recurso] = interaction.user
    iniciar_timer(recurso)
    await interaction.response.send_message(f"🔌 Você iniciou o uso de **{recurso}**.")
    await logar(f"{interaction.user.mention} iniciou o uso de **{recurso}** via comando.")
    await atualizar_status()

@bot.tree.command(name="encerraruso")
@app_commands.describe(recurso="Nome do recurso para encerrar uso")
async def encerraruso(interaction: discord.Interaction, recurso: str):
    if recurso not in recursos:
        await interaction.response.send_message("❌ Esse recurso não existe.", ephemeral=True)
        return
    if recursos[recurso] != interaction.user:
        await interaction.response.send_message("🚫 Você não está usando esse recurso.", ephemeral=True)
        return
    recursos[recurso] = None
    cancelar_timer(recurso)
    await interaction.response.send_message(f"❌ Você encerrou o uso de **{recurso}**.")
    await logar(f"{interaction.user.mention} encerrou o uso de **{recurso}** via comando.")
    await atualizar_status()

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    await atualizar_status()
    try:
        synced = await bot.tree.sync()
        print(f"✅ Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Erro ao sincronizar comandos: {e}")
    print(f"🤖 Bot conectado como {bot.user}")

bot.run(TOKEN)
