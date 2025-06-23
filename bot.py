import asyncio
import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

print ("o bot está funcionando")

TOKEN = os.getenv("TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    print("❌ ERRO: TOKEN não encontrado nas variáveis de ambiente!")
    print("Por favor, adicione seu token do Discord bot nas Secrets.")
    exit(1)

print("Token configurado:", "✅" if TOKEN else "❌")

CANAL_ID_HOSPEDAGEM = int("1386760046456868925")
CANAL_ID_LOGS = int("1386793302623391814")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

recursos = {
    "BR Data APP 01": None,
    "BR Data App 02": None,

    "BR Data - BD (SQL/Mongo)": None,   
    "BR Data - Compartilhada": None,
    "Soul - App 01": None,
    "Soul - BD SQL": None,
    "Soul - BD MongoDB": None
}

timers = {}
canais_temporarios = {}  # {(usuario_id, recurso): canal_id}

class BotaoDesconectar(discord.ui.View):
    def __init__(self, recurso):
        super().__init__(timeout=None)
        self.recurso = recurso

    @discord.ui.button(label="🔌 Desconectar", style=discord.ButtonStyle.red)
    async def desconectar_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            usuario = interaction.user
            if recursos[self.recurso] == usuario:
                # Responder ANTES de deletar o canal
                await interaction.response.send_message("❌ Desconectado com sucesso!", ephemeral=True)
                
                recursos[self.recurso] = None
                cancelar_timer(self.recurso)
                await logar(f"{usuario.mention} desconectou do **{self.recurso}** via botão")
                await atualizar_status()
                await deletar_canal_temporario(usuario, self.recurso)
            else:
                await interaction.response.send_message("🚫 Você não está conectado a este recurso.", ephemeral=True)
        except discord.errors.NotFound:
            # Se a interação já expirou ou o canal foi deletado, apenas fazer o cleanup
            print("⚠️ Interação expirada, fazendo cleanup silencioso")
            if recursos[self.recurso] == usuario:
                recursos[self.recurso] = None
                cancelar_timer(self.recurso)
                await logar(f"{usuario.mention} desconectou do **{self.recurso}** via botão (cleanup)")
                await atualizar_status()
                await deletar_canal_temporario(usuario, self.recurso)
        except Exception as e:
            print(f"❌ Erro ao desconectar via botão: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Erro ao desconectar.", ephemeral=True)
            except:
                # Se não conseguir responder, pelo menos fazer o cleanup
                if recursos[self.recurso] == usuario:
                    recursos[self.recurso] = None
                    cancelar_timer(self.recurso)
                    await deletar_canal_temporario(usuario, self.recurso)

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
    async def select_callback(self, interaction, select):
        try:
            recurso = select.values[0]
            usuario = interaction.user

            if recursos[recurso] is None:
                recursos[recurso] = usuario
                await interaction.response.send_message(f"🔌 Você se conectou ao **{recurso}**.", ephemeral=True, delete_after=5)
                await logar(f"{usuario.mention} conectou ao **{recurso}**")
                iniciar_timer(recurso)
                await criar_canal_temporario(usuario, recurso)
            elif recursos[recurso] == usuario:
                recursos[recurso] = None
                await interaction.response.send_message(f"❌ Você se desconectou do **{recurso}**.", ephemeral=True, delete_after=5)
                await logar(f"{usuario.mention} desconectou do **{recurso}**")
                cancelar_timer(recurso)
                await deletar_canal_temporario(usuario, recurso)
            else:
                # This means recursos[recurso] is not None and not the current user
                if recursos[recurso] and hasattr(recursos[recurso], 'mention'):
                    await interaction.response.send_message(f"🚫 O **{recurso}** já está em uso por {recursos[recurso].mention}.", ephemeral=True)
                else:
                    await interaction.response.send_message(f"🚫 O **{recurso}** já está em uso.", ephemeral=True)

            await atualizar_status()
        except Exception as e:
            print(f"❌ Erro no select_callback: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Ocorreu um erro. Tente novamente.", ephemeral=True)

async def atualizar_status():
    try:
        canal = bot.get_channel(CANAL_ID_HOSPEDAGEM)
        if not canal:
            print(f"❌ Canal de hospedagem não encontrado: {CANAL_ID_HOSPEDAGEM}")
            return
            
        msg_id = await buscar_msg_fixa(canal)
        conteudo = "**💻 Status das Conexões:**\n\n"

        for nome, usuario in recursos.items():
            if usuario and hasattr(usuario, 'mention'):
                conteudo += f"{nome}: 🔴 Em uso por {usuario.mention}\n"
            else:
                conteudo += f"{nome}: ✅ Disponível\n"

        view = MenuConexao()

        if msg_id:
            try:
                msg = await canal.fetch_message(msg_id)
                await msg.edit(content=conteudo, view=view)
            except discord.NotFound:
                nova_msg = await canal.send(content=conteudo, view=view)
                await nova_msg.pin()
        else:
            nova_msg = await canal.send(content=conteudo, view=view)
            await nova_msg.pin()
    except Exception as e:
        print(f"❌ Erro ao atualizar status: {e}")

async def buscar_msg_fixa(canal):
    pins = await canal.pins()
    for msg in pins:
        if msg.author == bot.user:
            return msg.id
    return None

async def logar(mensagem):
    try:
        canal = bot.get_channel(CANAL_ID_LOGS)
        if canal:
            await canal.send(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")
        else:
            print(f"❌ Canal de logs não encontrado: {CANAL_ID_LOGS}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")
    except Exception as e:
        print(f"❌ Erro ao enviar log: {e}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")

def iniciar_timer(recurso):
    async def desconectar():
        await asyncio.sleep(14400)  # 4 horas = 14400 segundos
        if recursos[recurso] and hasattr(recursos[recurso], 'mention'):
            usuario = recursos[recurso]
            await logar(f"⏱ Tempo expirado: {usuario.mention} foi desconectado de **{recurso}**.")
            await deletar_canal_temporario(usuario, recurso)
            recursos[recurso] = None
            await atualizar_status()
    timers[recurso] = asyncio.create_task(desconectar())

def cancelar_timer(recurso):
    if recurso in timers and timers[recurso]:
        timers[recurso].cancel()
        timers[recurso] = None

async def criar_canal_temporario(usuario, recurso):
    try:
        # Pegar o canal de hospedagem
        canal_hospedagem = bot.get_channel(CANAL_ID_HOSPEDAGEM)
        if not canal_hospedagem:
            print(f"❌ Canal de hospedagem não encontrado: {CANAL_ID_HOSPEDAGEM}")
            return
        
        # Nome da thread temporária
        nome_thread = f"🔌 {usuario.display_name} - {recurso}"
        
        # Criar thread privada no canal de hospedagem
        thread = await canal_hospedagem.create_thread(
            name=nome_thread,
            type=discord.ChannelType.private_thread,
            reason=f"Conexão temporária de {usuario.display_name} ao {recurso}"
        )
        
        # Adicionar o usuário à thread
        await thread.add_user(usuario)
        
        # Salvar referência da thread
        canais_temporarios[(usuario.id, recurso)] = thread.id
        
        # Enviar mensagem de boas-vindas com botão de desconectar
        embed = discord.Embed(
            title="🔌 Conexão Ativa",
            description=f"Você está conectado ao **{recurso}**",
            color=discord.Color.green()
        )
        embed.add_field(
            name="⏱️ Tempo Limite", 
            value="4 horas (desconexão automática)", 
            inline=False
        )
        embed.add_field(
            name="📝 Como Desconectar", 
            value="• Clique no botão 🔌 Desconectar abaixo\n• Use o comando `/encerraruso`", 
            inline=False
        )
        
        view = BotaoDesconectar(recurso)
        await thread.send(f"Olá {usuario.mention}!", embed=embed, view=view)
        
        print(f"✅ Thread temporária criada: {thread.name}")
        
    except Exception as e:
        print(f"❌ Erro ao criar thread temporária: {e}")

async def deletar_canal_temporario(usuario, recurso):
    try:
        chave_canal = (usuario.id, recurso)
        if chave_canal in canais_temporarios:
            thread_id = canais_temporarios[chave_canal]
            thread = bot.get_channel(thread_id)
            
            if thread and isinstance(thread, discord.Thread):
                await thread.delete()
                print(f"✅ Thread temporária deletada: {thread.name}")
            
            # Remove da lista
            del canais_temporarios[chave_canal]
            
    except Exception as e:
        print(f"❌ Erro ao deletar thread temporária: {e}")

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
    await deletar_canal_temporario(interaction.user, recurso)
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
