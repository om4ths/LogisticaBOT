import asyncio
import os
from datetime import datetime, timedelta
from threading import Thread # Reintroduzindo Thread para o bot

import discord
from discord import app_commands
from discord.ext import commands, tasks

# Importa Flask
from flask import Flask

print("O bot está funcionando")

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
# Adicionar intents de membros para poder buscar usuários por ID para DMs
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Dicionário para armazenar o status dos recursos (quem está usando)
recursos = {
    "BR Data APP 01": None,
    "BR Data App 02": None,
    "BR Data - BD (SQL/Mongo)": None,
    "BR Data - Compartilhada": None,
    "Soul - App 01": None,
    "Soul - App 02": None,
    "Soul - BD SQL": None,
    "Soul - BD MongoDB": None
}

# Dicionário para armazenar os timers de desconexão automática
timers = {}
# Dicionário para armazenar os IDs dos canais temporários (threads de conexão ativa)
canais_temporarios = {}  # {(usuario_id, recurso): canal_id}
# Dicionário para armazenar as filas de espera para cada recurso
filas = {
    nome: asyncio.Queue()
    for nome in recursos
}  # Cada recurso tem sua própria fila
# NOVO: Dicionário para armazenar os IDs dos canais temporários de fila
canais_fila_temporarios = {}  # {(usuario_id, recurso): canal_id}


class BotaoDesconectar(discord.ui.View):
    """View para o botão de desconectar dentro da thread temporária de conexão ativa."""

    def __init__(self, recurso):
        super().__init__(timeout=None)
        self.recurso = recurso
        # Criar o botão dinamicamente dentro do __init__ para acessar 'recurso'
        button = discord.ui.Button(label="🔌 Desconectar",
                                   style=discord.ButtonStyle.red,
                                   custom_id=f"desconectar_{recurso}")
        button.callback = self.desconectar_button  # Atribuir o callback ao botão
        self.add_item(button)  # Adicionar o botão à view

    async def desconectar_button(self, interaction: discord.Interaction):
        # Deferir a resposta imediatamente para evitar "Interaction Failed"
        await interaction.response.defer(ephemeral=True) # Deferir como efêmero

        usuario_interacao = interaction.user # Captura o usuário da interação
        try:
            # Ao comparar, compare o ID do usuário para robustez
            if recursos[self.recurso] == usuario_interacao.id: # Compara com o ID armazenado
                await logar(
                    f"{usuario_interacao.mention} desconectou do **{self.recurso}** via botão"
                )
                recursos[self.recurso] = None
                cancelar_timer(self.recurso)
                await deletar_canal_temporario(
                    usuario_interacao, self.recurso)  # Deleta o canal de conexão ativa
                await atualizar_status()  # Atualiza o status principal
                await verificar_fila(self.recurso
                                     )  # Verifica a fila após liberação
                await interaction.followup.send(
                    "❌ Desconectado com sucesso!", ephemeral=True, delete_after=5) # Usar followup.send
            else:
                await interaction.followup.send(
                    "🚫 Você não está conectado a este recurso.",
                    ephemeral=True, delete_after=5) # Usar followup.send
        except discord.errors.NotFound:
            # Se a interação já expirou ou o canal foi deletado, fazer o cleanup silencioso
            print(
                "⚠️ Interação expirada ou canal não encontrado, fazendo cleanup silencioso."
            )
            if recursos[self.recurso] == usuario_interacao.id: # Compara com o ID armazenado
                recursos[self.recurso] = None
                cancelar_timer(self.recurso)
                await logar(
                    f"{usuario_interacao.mention} desconectou do **{self.recurso}** via botão (cleanup silencioso)."
                )
                await deletar_canal_temporario(usuario_interacao, self.recurso)
                await atualizar_status()
                await verificar_fila(self.recurso)
        except Exception as e:
            print(
                f"❌ Erro ao desconectar via botão para {usuario_interacao.name} do {self.recurso}: {e}"
            )
            if not interaction.response.is_done(): # Verifica se a resposta já foi enviada
                await interaction.followup.send( # Usar followup.send
                    "❌ Ocorreu um erro ao desconectar. Tente novamente.",
                    ephemeral=True, delete_after=5)
            # Tentar fazer o cleanup mesmo em caso de erro
            if recursos[self.recurso] == usuario_interacao.id: # Compara com o ID armazenado
                recursos[self.recurso] = None
                cancelar_timer(self.recurso)
                await deletar_canal_temporario(usuario_interacao, self.recurso)
                await atualizar_status()
                await verificar_fila(self.recurso)


class ConfirmarFilaView(discord.ui.View):
    """View para confirmar entrada na fila."""

    def __init__(self, recurso, usuario_id):
        super().__init__(timeout=60)  # Timeout de 60 segundos para resposta
        self.recurso = recurso
        self.usuario_id = usuario_id
        self.value = None  # Para armazenar a escolha do usuário

    @discord.ui.button(label="Sim, entrar na fila",
                       style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction,
                             button: discord.ui.Button):
        # Não deferir aqui, pois a resposta é uma edição da mensagem original (que já foi deferida ou respondida)
        if interaction.user.id != self.usuario_id:
            await interaction.response.send_message(
                "🚫 Esta interação não é para você.", ephemeral=True, delete_after=5)
            return
        self.value = True
        self.stop()
        await interaction.response.edit_message(
            content="✅ Você optou por entrar na fila.", view=None)

    @discord.ui.button(label="Não", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: discord.Interaction,
                            button: discord.ui.Button):
        # Não deferir aqui
        if interaction.user.id != self.usuario_id:
            await interaction.response.send_message(
                "🚫 Esta interação não é para você.", ephemeral=True, delete_after=5)
            return
        self.value = False
        self.stop()
        await interaction.response.edit_message(
            content="❌ Você optou por não entrar na fila.", view=None)


class QueueThreadView(discord.ui.View):
    """NOVO: View para o botão de sair da fila dentro da thread temporária de fila."""

    def __init__(self, recurso, usuario_id):
        super().__init__(timeout=None)  # Pode ser persistente
        self.recurso = recurso
        self.usuario_id = usuario_id
        # Criar o botão dinamicamente
        button = discord.ui.Button(
            label="🚶 Sair da Fila",
            style=discord.ButtonStyle.red,
            custom_id=f"sairfila_btn_{recurso}_{usuario_id}")
        button.callback = self.sair_fila_button  # Atribuir o callback
        self.add_item(button)  # Adicionar o botão à view

    async def sair_fila_button(self, interaction: discord.Interaction):
        # Deferir a resposta imediatamente
        await interaction.response.defer(ephemeral=True) # Deferir como efêmero

        usuario_interacao = interaction.user # Captura o usuário da interação
        if usuario_interacao.id != self.usuario_id:
            await interaction.followup.send( # Usar followup.send
                "🚫 Esta interação não é para você.", ephemeral=True, delete_after=5)
            return

        try:
            # Remover o usuário da fila
            fila_atual = list(filas[self.recurso]._queue)
            if usuario_interacao.id in fila_atual:
                nova_fila = [uid for uid in fila_atual if uid != usuario_interacao.id]
                filas[self.recurso] = asyncio.Queue()  # Recria a fila
                for uid in nova_fila:
                    await filas[self.recurso].put(uid)

                await logar(
                    f"{usuario_interacao.mention} saiu da fila para **{self.recurso}** via botão na thread."
                )
                await deletar_canal_fila_temporario(
                    usuario_interacao, self.recurso)  # Deleta a thread da fila
                await atualizar_status()
                await interaction.followup.send( # Usar followup.send
                    f"❌ Você saiu da fila para **{self.recurso}**.",
                    ephemeral=True, delete_after=5)
            else:
                await interaction.followup.send( # Usar followup.send
                    f"Você não está mais na fila para **{self.recurso}**.",
                    ephemeral=True, delete_after=5)
        except Exception as e:
            print(
                f"❌ Erro ao sair da fila via botão para {usuario_interacao.name} do {self.recurso}: {e}"
            )
            if not interaction.response.is_done():
                await interaction.followup.send( # Usar followup.send
                    "❌ Ocorreu um erro ao sair da fila. Tente novamente.",
                    ephemeral=True, delete_after=5)


class MenuConexao(discord.ui.View):
    """View para o menu suspenso de seleção de recursos."""

    def __init__(self):
        super().__init__(timeout=None)  # Timeout None para ser persistente

    @discord.ui.select(placeholder="Selecione o servidor para se conectar",
                       min_values=1,
                       max_values=1,
                       options=[
                           discord.SelectOption(
                               label=nome, description="Clique para conectar")
                           for nome in recursos
                       ])
    async def select_callback(self, interaction: discord.Interaction,
                              select: discord.ui.Select):
        # Deferir a resposta imediatamente, antes de qualquer lógica demorada
        await interaction.response.defer(ephemeral=True) # Deferir como efêmero

        recurso = select.values[0]
        usuario_interacao = interaction.user # Captura o usuário da interação

        try:
            if recursos[recurso] is None:
                # Recurso liberado, conectar o usuário
                recursos[recurso] = usuario_interacao.id # Armazena o ID do usuário
                await logar(f"{usuario_interacao.mention} conectou ao **{recurso}**")
                iniciar_timer(recurso)
                await criar_canal_temporario(
                    usuario_interacao, recurso)  # Cria a thread de conexão ativa
                await interaction.followup.send( # Usar followup.send
                    f"🔌 Você se conectou ao **{recurso}**.", ephemeral=True, delete_after=5)
            # Ao comparar, compare o ID do usuário para robustez
            elif recursos[recurso] == usuario_interacao.id: 
                # Usuário já conectado, desconectar
                await logar(f"{usuario_interacao.mention} desconectou do **{recurso}**")
                recursos[recurso] = None
                cancelar_timer(recurso)
                await deletar_canal_temporario(
                    usuario_interacao, recurso)  # Deleta a thread de conexão ativa
                await verificar_fila(recurso
                                     )  # Verifica a fila após desconexão
                await interaction.followup.send( # Usar followup.send
                    f"❌ Você se desconectou do **{recurso}**.", ephemeral=True, delete_after=5)
            else:
                # Recurso em uso por outra pessoa, oferecer fila
                ocupante_id = recursos[recurso] # Pega o ID do ocupante
                ocupante_mention = "outro usuário"
                if ocupante_id: # Tenta resolver a menção se o ID existe
                    try:
                        ocupante_obj = await bot.fetch_user(ocupante_id) # Tenta buscar o objeto usuário
                        ocupante_mention = ocupante_obj.mention
                    except discord.NotFound:
                        ocupante_mention = "[Usuário Desconhecido]" # Fallback se o usuário não for encontrado

                # Verificar se o usuário já está na fila
                fila_atual = list(
                    filas[recurso]._queue
                )  # Acessar a fila interna (não recomendado para modificação, mas ok para leitura)
                if usuario_interacao.id in fila_atual:
                    await interaction.followup.send( # Usar followup.send
                        f"Você já está na fila para **{recurso}**. Sua posição: {fila_atual.index(usuario_interacao.id) + 1}.",
                        ephemeral=True, delete_after=5)
                    await atualizar_status()  # Atualiza o status para garantir
                    return

                view = ConfirmarFilaView(recurso, usuario_interacao.id)
                # A mensagem com os botões deve ser enviada via followup.send após o defer
                await interaction.followup.send(
                    f"🚫 O **{recurso}** já está em uso por {ocupante_mention}. Deseja entrar na fila?",
                    view=view,
                    ephemeral=True, delete_after=60) # delete_after aqui é para a mensagem de pergunta

                # Esperar pela resposta do usuário. A resposta do botão será uma edição da mensagem original.
                await view.wait()

                # As respostas subsequentes (após a espera do botão) já devem ser followup.send
                if view.value is True:
                    await filas[recurso].put(
                        usuario_interacao.id)  # Adiciona o ID do usuário à fila
                    posicao_na_fila = list(filas[recurso]._queue).index(
                        usuario_interacao.id) + 1  # Posição correta
                    await logar(
                        f"{usuario_interacao.mention} entrou na fila para **{recurso}**. Posição: {posicao_na_fila}"
                    )
                    # NOVO: Cria a thread da fila em vez de enviar mensagem efêmera
                    await criar_canal_fila_temporario(usuario_interacao, recurso,
                                                      posicao_na_fila)
                    await interaction.followup.send(
                        f"✅ Você entrou na fila para **{recurso}**. Verifique seu canal temporário de fila.",
                        ephemeral=True, delete_after=5)
                elif view.value is False:
                    await interaction.followup.send(
                        f"Você optou por não entrar na fila para **{recurso}**.",
                        ephemeral=True, delete_after=5)
                else:  # Timeout
                    await interaction.followup.send(
                        f"Tempo esgotado. Você não entrou na fila para **{recurso}**.",
                        ephemeral=True, delete_after=5)

            await atualizar_status(
            )  # Sempre atualizar o status após qualquer mudança de conexão/fila
        except Exception as e:
            print(
                f"❌ Erro no select_callback para {usuario_interacao.name} e {recurso}: {e}"
            )
            if not interaction.response.is_done(): # Verifica se a resposta já foi enviada
                await interaction.followup.send( # Usar followup.send
                    "❌ Ocorreu um erro. Tente novamente.", ephemeral=True, delete_after=5)


async def atualizar_status():
    """Atualiza a mensagem fixa no canal de hospedagem com o status dos recursos."""
    try:
        canal = bot.get_channel(CANAL_ID_HOSPEDAGEM)
        if not canal:
            print(
                f"❌ Canal de hospedagem não encontrado: {CANAL_ID_HOSPEDAGEM}")
            return

        msg_id = await buscar_msg_fixa(canal)
        conteudo = "**💻 Status das Conexões:**\n\n"  # Mantém o emoji de laptop aqui

        for nome, usuario_id in recursos.items(): # Itera sobre o ID do usuário
            fila_len = filas[nome].qsize()
            fila_info = f" ({fila_len} na fila)" if fila_len > 0 else ""

            # Ajuste para o layout e emojis da imagem
            conteudo += f"💻 **{nome}**{fila_info}\n"  # Emoji de laptop antes do nome do recurso

            if usuario_id is not None: # Se há um ID de usuário armazenado
                try:
                    # Tenta buscar o objeto User/Member completo a partir do ID
                    usuario_obj = await bot.fetch_user(usuario_id) 
                    conteudo += f"🔴 Em uso por {usuario_obj.mention}\n"  # Usa a menção do objeto completo
                except discord.NotFound:
                    # Se o usuário não for encontrado (ex: saiu do servidor), mostra como desconhecido
                    conteudo += f"🔴 Em uso por [Usuário Desconhecido]\n"
                except Exception as e:
                    # Outro erro ao buscar o usuário
                    print(f"❌ Erro ao buscar usuário {usuario_id} para status: {e}")
                    conteudo += f"🔴 Em uso por [Erro ao carregar usuário]\n"
            else:
                conteudo += f"✅ Liberado!\n"  # Emoji de check mark verde para liberado
            # NENHUMA linha em branco extra aqui, para evitar espaçamento duplo

        view = MenuConexao()

        if msg_id:
            try:
                msg = await canal.fetch_message(msg_id)
                await msg.edit(content=conteudo, view=view)
            except discord.NotFound:
                # Mensagem não encontrada, criar uma nova
                nova_msg = await canal.send(content=conteudo, view=view)
                await nova_msg.pin()
            except Exception as edit_error:
                print(
                    f"❌ Erro ao editar mensagem fixa: {edit_error}. Tentando criar nova."
                )
                nova_msg = await canal.send(content=conteudo, view=view)
                await nova_msg.pin()
        else:
            # Nenhuma mensagem fixa encontrada, criar uma nova
            nova_msg = await canal.send(content=conteudo, view=view)
            await nova_msg.pin()
    except Exception as e:
        print(f"❌ Erro ao atualizar status: {e}")


async def buscar_msg_fixa(canal):
    """Busca a mensagem fixa do bot no canal."""
    pins = await canal.pins()
    for msg in pins:
        if msg.author == bot.user:
            return msg.id
    return None


async def logar(mensagem):
    """Envia uma mensagem para o canal de logs e imprime no console."""
    try:
        canal = bot.get_channel(CANAL_ID_LOGS)
        if canal:
            await canal.send(
                f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}")
        else:
            print(f"❌ Canal de logs não encontrado: {CANAL_ID_LOGS}")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}"
                  )  # Imprime no console mesmo sem canal de logs
    except Exception as e:
        print(f"❌ Erro ao enviar log: {e}")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensagem}"
              )  # Imprime no console em caso de erro


def iniciar_timer(recurso):
    """Inicia um timer de 4 horas para desconexão automática do recurso."""

    async def desconectar_apos_tempo():
        # Captura o ID do usuário antes do sleep, caso o objeto mude ou seja removido
        usuario_id_no_momento = recursos[recurso] 
        if usuario_id_no_momento is None: # Recurso já liberado manualmente
            return

        await asyncio.sleep(14400)  # 4 horas = 14400 segundos

        # Verifica novamente se o ID do usuário ainda é o mesmo após o sleep
        if recursos[recurso] == usuario_id_no_momento: 
            # Tenta buscar o objeto usuário para o log e deleção de thread
            try:
                usuario_obj = await bot.fetch_user(usuario_id_no_momento)
                await logar(
                    f"⏱ Tempo expirado: {usuario_obj.mention} foi desconectado de **{recurso}**."
                )
                await deletar_canal_temporario(
                    usuario_obj, recurso)  # Deleta a thread de conexão ativa
            except discord.NotFound:
                await logar(f"⏱ Tempo expirado: Usuário com ID {usuario_id_no_momento} não encontrado. Desconectado de **{recurso}**.")
            except Exception as e:
                await logar(f"❌ Erro ao processar desconexão por tempo para {usuario_id_no_momento} do {recurso}: {e}")

            recursos[recurso] = None
            await atualizar_status()
            await verificar_fila(recurso
                                 )  # Verifica a fila após desconexão por tempo
        else:
            # O recurso já foi liberado ou ocupado por outro usuário manualmente
            # antes do timer expirar. Não faz nada para evitar conflitos.
            pass 

    # Cancela qualquer timer existente para este recurso antes de iniciar um novo
    cancelar_timer(recurso)
    timers[recurso] = asyncio.create_task(desconectar_apos_tempo())


def cancelar_timer(recurso):
    """Cancela o timer de desconexão automática para um recurso."""
    if recurso in timers and timers[recurso]:
        timers[recurso].cancel()
        timers[recurso] = None


async def criar_canal_temporario(usuario: discord.User, recurso: str):
    """Cria uma thread privada para o usuário conectado ao recurso (conexão ativa)."""
    try:
        canal_hospedagem = bot.get_channel(CANAL_ID_HOSPEDAGEM)
        if not canal_hospedagem:
            print(
                f"❌ Canal de hospedagem não encontrado: {CANAL_ID_HOSPEDAGEM}")
            return

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
            color=discord.Color.green())
        embed.add_field(name="⏱️ Tempo Limite",
                        value="4 horas (desconexão automática)",
                        inline=False)
        embed.add_field(
            name="📝 Como Desconectar",
            value=
            "• Clique no botão 🔌 Desconectar abaixo\n• Use o comando `/encerraruso`",
            inline=False)

        view = BotaoDesconectar(recurso)
        await thread.send(f"Olá {usuario.mention}!", embed=embed, view=view)

        print(f"✅ Thread temporária de conexão criada: {thread.name}")

    except Exception as e:
        print(
            f"❌ Erro ao criar thread temporária de conexão para {usuario.name} e {recurso}: {e}"
        )


async def deletar_canal_temporario(usuario: discord.User, recurso: str):
    """Deleta a thread privada associada ao usuário e recurso (conexão ativa)."""
    try:
        chave_canal = (usuario.id, recurso)
        if chave_canal in canais_temporarios:
            thread_id = canais_temporarios[chave_canal]
            thread = bot.get_channel(thread_id)

            if thread and isinstance(thread, discord.Thread):
                try:
                    await thread.delete()
                    print(
                        f"✅ Thread temporária de conexão deletada: {thread.name}"
                    )
                except discord.Forbidden:
                    print(
                        f"❌ Sem permissão para deletar thread: {thread.name}")
                except Exception as delete_error:
                    print(
                        f"❌ Erro ao deletar thread {thread.name}: {delete_error}"
                    )

            # Remove da lista independentemente
            del canais_temporarios[chave_canal]

    except Exception as e:
        print(
            f"❌ Erro ao deletar thread temporária de conexão para {usuario.name} e {recurso}: {e}"
        )


# NOVO: Funções para gerenciar threads de fila
async def criar_canal_fila_temporario(usuario: discord.User, recurso: str,
                                      posicao: int):
    """Cria uma thread privada para o usuário na fila."""
    try:
        canal_hospedagem = bot.get_channel(CANAL_ID_HOSPEDAGEM)
        if not canal_hospedagem:
            print(
                f"❌ Canal de hospedagem não encontrado: {CANAL_ID_HOSPEDAGEM}")
            return

        nome_thread = f"⏳ {usuario.display_name} - Fila {recurso}"

        thread = await canal_hospedagem.create_thread(
            name=nome_thread,
            type=discord.ChannelType.private_thread,
            reason=f"Fila para {recurso} de {usuario.display_name}")

        await thread.add_user(usuario)

        canais_fila_temporarios[(usuario.id, recurso)] = thread.id

        embed = discord.Embed(
            title="⏳ Você está na Fila!",
            description=f"Você entrou na fila para o recurso **{recurso}**.",
            color=discord.Color.orange())
        embed.add_field(name="Sua Posição na Fila",
                        value=f"**#{posicao}**",
                        inline=False)
        embed.add_field(
            name="Aguarde",
            value=
            "Você será notificado automaticamente aqui quando for a sua vez de usar o recurso.",
            inline=False)
        embed.add_field(
            name="Sair da Fila",
            value=
            "Se não quiser mais esperar, clique no botão abaixo ou use `/sairfila`.",
            inline=False)

        view = QueueThreadView(recurso, usuario.id)
        await thread.send(f"Olá {usuario.mention}!", embed=embed, view=view)

        print(f"✅ Thread temporária de fila criada: {thread.name}")

    except Exception as e:
        print(
            f"❌ Erro ao criar thread temporária de fila para {usuario.name} e {recurso}: {e}"
        )


async def deletar_canal_fila_temporario(usuario: discord.User, recurso: str):
    """Deleta a thread privada associada ao usuário e recurso (fila)."""
    try:
        chave_canal = (usuario.id, recurso)
        if chave_canal in canais_fila_temporarios:
            thread_id = canais_fila_temporarios[chave_canal]
            thread = bot.get_channel(thread_id)

            if thread and isinstance(thread, discord.Thread):
                try:
                    await thread.delete()
                    print(
                        f"✅ Thread temporária de fila deletada: {thread.name}")
                except discord.Forbidden:
                    print(
                        f"❌ Sem permissão para deletar thread: {thread.name}")
                except Exception as delete_error:
                    print(
                        f"❌ Erro ao deletar thread {thread.name}: {delete_error}"
                    )

            # Remove da lista independentemente
            del canais_fila_temporarios[chave_canal]

    except Exception as e:
        print(
            f"❌ Erro ao deletar thread temporária de fila para {usuario.name} e {recurso}: {e}"
        )


async def verificar_fila(recurso: str):
    """Verifica a fila de um recurso e conecta/notifica o próximo usuário, se houver."""
    # Garante que o recurso está realmente livre antes de tentar conectar alguém da fila
    if recursos[recurso] is not None:
        return  # Recurso ainda em uso, não faz nada com a fila neste momento

    if not filas[recurso].empty():
        proximo_usuario_id = await filas[recurso].get(
        )  # Remove o primeiro da fila
        try:
            # Tenta buscar o usuário. Se não for encontrado (ex: saiu do servidor), loga e tenta o próximo.
            proximo_usuario = await bot.fetch_user(proximo_usuario_id)

            if proximo_usuario:
                # Verifica novamente se o recurso ainda está livre antes de conectar
                if recursos[recurso] is None:
                    recursos[recurso] = proximo_usuario.id # Armazena o ID do usuário
                    iniciar_timer(recurso)
                    await criar_canal_temporario(
                        proximo_usuario,
                        recurso)  # Cria a thread de conexão ativa
                    await deletar_canal_fila_temporario(
                        proximo_usuario,
                        recurso)  # NOVO: Deleta a thread da fila

                    # Envia uma mensagem para a nova thread do usuário (ou DM como fallback)
                    thread_id = canais_temporarios.get(
                        (proximo_usuario.id, recurso))
                    if thread_id:
                        thread = bot.get_channel(thread_id)
                        if thread:
                            await thread.send(
                                f"🎉 {proximo_usuario.mention}, o recurso **{recurso}** está agora **AUTOMATICAMENTE CONECTADO** para você! "
                                "Este é o seu canal temporário para uso. Lembre-se do tempo limite de 4 horas."
                            )
                        else:  # Thread não encontrada após criação, envia DM como fallback
                            await proximo_usuario.send(
                                f"🎉 O recurso **{recurso}** está agora **AUTOMATICAMENTE CONECTADO** para você! "
                                "Houve um problema ao criar seu canal temporário, mas você está conectado."
                            )
                    else:  # Sem ID da thread, envia DM como fallback
                        await proximo_usuario.send(
                            f"🎉 O recurso **{recurso}** está agora **AUTOMATICAMENTE CONECTADO** para você! "
                            "Houve um problema ao criar seu canal temporário, mas você está conectado."
                        )

                    await logar(
                        f"🔔 {proximo_usuario.mention} foi automaticamente conectado a **{recurso}** da fila."
                    )
                    await atualizar_status(
                    )  # Atualiza o status após a conexão automática
                else:
                    # Cenário de corrida: recurso foi ocupado por outro antes da conexão da fila
                    await logar(
                        f"⚠️ Recurso {recurso} foi ocupado por outro antes que {proximo_usuario.mention} pudesse ser conectado da fila."
                    )
                    await proximo_usuario.send(
                        f"🚫 Infelizmente, o recurso **{recurso}** foi ocupado novamente antes que você pudesse ser conectado automaticamente. "
                        "Por favor, tente novamente ou entre na fila se desejar."
                    )
                    # Verifica a fila novamente, pois o recurso ainda está ocupado
                    await verificar_fila(recurso)
            else:
                # Usuário não encontrado, tenta o próximo na fila
                await logar(
                    f"⚠️ Usuário com ID {proximo_usuario_id} não encontrado (provavelmente saiu do servidor) para conexão automática da fila de {recurso}."
                )
                await verificar_fila(recurso)
        except discord.NotFound:
            # Erro específico para usuário não encontrado, trata como acima
            await logar(
                f"⚠️ Usuário com ID {proximo_usuario_id} não encontrado (provavelmente saiu do servidor) para conexão automática da fila de {recurso}."
            )
            await verificar_fila(recurso)  # Tenta o próximo
        except Exception as e:
            await logar(
                f"❌ Erro ao tentar conectar usuário da fila para {recurso}: {e}"
            )
            # Tenta enviar DM sobre o erro, se possível
            try:
                if proximo_usuario:
                    await proximo_usuario.send(
                        f"❌ Ocorreu um erro ao tentar conectar você automaticamente ao recurso **{recurso}**. "
                        "Por favor, tente se conectar manualmente ou entre em contato com um administrador."
                    )
            except Exception as dm_e:
                print(f"❌ Erro ao enviar DM de erro: {dm_e}")
            await verificar_fila(recurso
                                 )  # Verifica a fila novamente em caso de erro


# --- Comandos de Barra (Slash Commands) ---


@bot.tree.command(name="iniciaruso")
@app_commands.describe(recurso="Nome do recurso para se conectar")
async def iniciaruso(interaction: discord.Interaction, recurso: str):
    """Conecta o usuário a um recurso específico."""
    # Deferir a resposta imediatamente
    await interaction.response.defer(ephemeral=True)

    if recurso not in recursos:
        await interaction.followup.send("❌ Esse recurso não existe.",
                                                ephemeral=True, delete_after=5)
        return

    if recursos[recurso] is not None:
        ocupante_id = recursos[recurso] # Pega o ID do ocupante
        ocupante_mention = "outro usuário"
        if ocupante_id: # Tenta resolver a menção se o ID existe
            try:
                ocupante_obj = await bot.fetch_user(ocupante_id)
                ocupante_mention = ocupante_obj.mention
            except discord.NotFound:
                ocupante_mention = "[Usuário Desconhecido]" # Fallback
            except Exception as e:
                print(f"❌ Erro ao buscar ocupante {ocupante_id} no /iniciaruso: {e}")
                ocupante_mention = "[Erro ao carregar usuário]"

        # Verificar se o usuário já está na fila
        fila_atual = list(filas[recurso]._queue)
        if interaction.user.id in fila_atual:
            await interaction.followup.send(
                f"Você já está na fila para **{recurso}**. Sua posição: {fila_atual.index(interaction.user.id) + 1}.",
                ephemeral=True, delete_after=5)
            return

        view = ConfirmarFilaView(recurso, interaction.user.id)
        await interaction.followup.send( # Usar followup.send
            f"🚫 O **{recurso}** já está em uso por {ocupante_mention}. Deseja entrar na fila?",
            view=view,
            ephemeral=True, delete_after=15)

        await view.wait()

        if view.value is True:
            await filas[recurso].put(interaction.user.id
                                     )  # Adiciona o ID do usuário à fila
            posicao_na_fila = list(filas[recurso]._queue).index(
                interaction.user.id) + 1  # Corrigido para interaction.user.id
            await logar(
                f"{interaction.user.mention} entrou na fila para **{recurso}**. Posição: {posicao_na_fila}"
            )
            # NOVO: Cria a thread da fila em vez de enviar mensagem efêmera
            await criar_canal_fila_temporario(interaction.user, recurso,
                                              posicao_na_fila)
            await interaction.followup.send(
                f"✅ Você entrou na fila para **{recurso}**. Verifique seu canal temporário de fila.",
                ephemeral=True, delete_after=5)
        elif view.value is False:
            await interaction.followup.send(
                f"Você optou por não entrar na fila para **{recurso}**.",
                ephemeral=True, delete_after=5)
        else:  # Timeout
            await interaction.followup.send(
                f"Tempo esgotado. Você não entrou na fila para **{recurso}**.",
                ephemeral=True, delete_after=5)

        await atualizar_status()
        return

    # Se o recurso estiver liberado, conectar diretamente
    recursos[recurso] = interaction.user.id # Armazena o ID do usuário
    iniciar_timer(recurso)
    await logar(
        f"{interaction.user.mention} iniciou o uso de **{recurso}** via comando."
    )
    await criar_canal_temporario(interaction.user, recurso)
    await atualizar_status()
    await interaction.followup.send( # Usar followup.send
        f"🔌 Você iniciou o uso de **{recurso}**.", ephemeral=True, delete_after=5)


@bot.tree.command(name="encerraruso")
@app_commands.describe(recurso="Nome do recurso para encerrar uso")
async def encerraruso(interaction: discord.Interaction, recurso: str):
    """Encerra o uso de um recurso específico."""
    # Deferir a resposta imediatamente
    await interaction.response.defer(ephemeral=True)

    if recurso not in recursos:
        await interaction.followup.send("❌ Esse recurso não existe.",
                                                ephemeral=True, delete_after=5)
        return
    # Ao comparar, compare o ID do usuário para robustez
    if recursos[recurso] != interaction.user.id: 
        await interaction.followup.send(
            "🚫 Você não está usando esse recurso.", ephemeral=True, delete_after=5)
        return
    recursos[recurso] = None
    cancelar_timer(recurso)
    await deletar_canal_temporario(interaction.user,
                                   recurso)  # Deleta a thread de conexão ativa
    await logar(
        f"{interaction.user.mention} encerrou o uso de **{recurso}** via comando."
    )
    await atualizar_status()
    await verificar_fila(recurso)  # Verifica a fila após liberação
    await interaction.followup.send(
        f"❌ Você encerrou o uso de **{recurso}**.", ephemeral=True, delete_after=5)


@bot.tree.command(name="entrarfila")
@app_commands.describe(recurso="Nome do recurso para entrar na fila")
async def entrarfila(interaction: discord.Interaction, recurso: str):
    """Permite ao usuário entrar na fila de um recurso."""
    # Deferir a resposta imediatamente
    await interaction.response.defer(ephemeral=True)

    if recurso not in recursos:
        await interaction.followup.send("❌ Esse recurso não existe.",
                                                ephemeral=True, delete_after=5)
        return

    # Ao comparar, compare o ID do usuário para robustez
    if recursos[recurso] == interaction.user.id: 
        await interaction.followup.send(
            f"Você já está conectado a **{recurso}**.", ephemeral=True, delete_after=5)
        return

    fila_atual = list(filas[recurso]._queue)
    if interaction.user.id in fila_atual:
        await interaction.followup.send(
            f"Você já está na fila para **{recurso}**. Sua posição: {fila_atual.index(interaction.user.id) + 1}.",
            ephemeral=True, delete_after=5)
        return

    await filas[recurso].put(interaction.user.id)
    posicao_na_fila = list(filas[recurso]._queue).index(
        interaction.user.id) + 1
    await logar(
        f"{interaction.user.mention} entrou na fila para **{recurso}** via comando."
    )
    # NOVO: Cria a thread da fila em vez de enviar mensagem efêmera
    await criar_canal_fila_temporario(interaction.user, recurso,
                                      posicao_na_fila)
    await atualizar_status()
    await interaction.followup.send(
        f"✅ Você entrou na fila para **{recurso}**. Verifique seu canal temporário de fila.",
        ephemeral=True, delete_after=5)


@bot.tree.command(name="sairfila")
@app_commands.describe(recurso="Nome do recurso para sair da fila")
async def sairfila(interaction: discord.Interaction, recurso: str):
    """Permite ao usuário sair da fila de um recurso."""
    # Deferir a resposta imediatamente
    await interaction.response.defer(ephemeral=True)

    if recurso not in recursos:
        await interaction.followup.send("❌ Esse recurso não existe.",
                                                ephemeral=True, delete_after=5)
        return

    fila_atual = list(filas[recurso]._queue)
    if interaction.user.id not in fila_atual:
        await interaction.followup.send(
            f"Você não está na fila para **{recurso}**.", ephemeral=True, delete_after=5)
        return

    # Remover o usuário da fila
    nova_fila = [uid for uid in fila_atual if uid != interaction.user.id]
    filas[recurso] = asyncio.Queue()  # Recria a fila
    for uid in nova_fila:
        await filas[recurso].put(uid)

    await logar(
        f"{interaction.user.mention} saiu da fila para **{recurso}** via comando."
    )
    await deletar_canal_fila_temporario(interaction.user, recurso
                                        )  # NOVO: Deleta a thread da fila
    await atualizar_status()
    await interaction.followup.send(
        f"❌ Você saiu da fila para **{recurso}**.", ephemeral=True, delete_after=5)


@bot.tree.command(name="verfila")
@app_commands.describe(recurso="Nome do recurso para ver a fila (opcional)")
async def verfila(interaction: discord.Interaction, recurso: str = None):
    """Exibe a fila de espera para um ou todos os recursos."""
    # Deferir a resposta imediatamente
    await interaction.response.defer(ephemeral=True)

    if recurso and recurso not in recursos:
        await interaction.followup.send("❌ Esse recurso não existe.",
                                                ephemeral=True, delete_after=5)
        return

    response_content = "**📊 Filas de Espera:**\n\n"
    recursos_para_verificar = [recurso] if recurso else recursos.keys()

    for r in recursos_para_verificar:
        fila_atual = list(filas[r]._queue)
        if not fila_atual:
            response_content += f"**{r}**: Fila vazia.\n"
        else:
            response_content += f"**{r}** ({len(fila_atual)} na fila):\n"
            for i, user_id in enumerate(fila_atual):
                try:
                    user = await bot.fetch_user(user_id)
                    response_content += f"  {i+1}. {user.display_name}\n"
                except discord.NotFound:
                    response_content += f"  {i+1}. Usuário desconhecido (ID: {user_id})\n"
                except Exception as e:
                    response_content += f"  {i+1}. Erro ao buscar usuário (ID: {user_id}): {e}\n"
        response_content += "\n"  # Adiciona uma linha em branco entre os recursos

    await interaction.followup.send(response_content, ephemeral=True)


# --- Eventos do Bot ---


@bot.event
async def on_ready():
    """Evento disparado quando o bot está pronto."""
    await bot.wait_until_ready()
    await atualizar_status()  # Atualiza o status ao iniciar
    try:
        # Sincroniza os comandos de barra com o Discord
        synced = await bot.tree.sync()
        print(f"✅ Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"❌ Erro ao sincronizar comandos: {e}")
    print(f"🤖 Bot conectado como {bot.user}")
    # Inicia a tarefa de persistência do Flask
    # REMOVIDO: manter_online() - Gunicorn gerencia o Flask no Render


# --- Manutenção Online (Flask) ---
app = Flask(__name__) # Use __name__ para o nome do módulo Flask

@app.route('/')
def home():
    # Este endpoint é usado pelo Render para health checks e pelo UptimeRobot
    return "Bot online!"

# Função para iniciar o bot do Discord em uma thread separada
def start_discord_bot():
    # O bot.run() é um método bloqueante, então precisa rodar em uma thread separada
    # para não bloquear o servidor Flask.
    bot.run(TOKEN)

# Inicia o bot do Discord em uma thread separada quando o Flask é iniciado pelo Gunicorn
# Isso garante que o Flask esteja escutando na porta para o Render, e o bot rode em paralelo.
discord_thread = Thread(target=start_discord_bot)
discord_thread.start()

# O Gunicorn irá servir o 'app' Flask. Nenhuma chamada app.run() é necessária aqui.
