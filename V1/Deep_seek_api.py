import os
import argparse
import json
from openai import OpenAI
from typing import List, Dict, Optional
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel
import signal
import httpx
from httpx_socks import SyncProxyTransport
from pathlib import Path
from pygments import highlight
from prompt_toolkit import PromptSession
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style
from pygments.lexers import get_lexer_by_name
from pygments.formatters import Terminal256Formatter

class ChatUI:
    def __init__(self):
        self.style = Style.from_dict({
            '': '#ansiblue',
            'prompt': '#ansicyan bold',
            'code': '#ansigreen'
        })
        self.history_file = os.path.expanduser('~/.chat_history')
        self.session = self._create_session()
        self.console = Console()
        
    def _create_session(self):
        """创建带有多行支持的提示会话"""
        bindings = KeyBindings()
        
        # Shift+Enter 换行
        @bindings.add(Keys.Escape, Keys.Enter)
        @bindings.add(Keys.ControlJ)
        def _(event):
            event.current_buffer.insert_text('\n')
            
        return PromptSession(
            history=FileHistory(self.history_file),
            auto_suggest=AutoSuggestFromHistory(),
            key_bindings=bindings,
            multiline=True,
            style=self.style
        )

    def display_prompt(self) -> str:
        """获取用户输入（支持多行和代码块）"""
        try:
            user_input = []
            
            # 获取初始输入
            first_line = self.session.prompt(
                message=[('class:prompt', 'User: ')],
                mouse_support=True
            )
            
            # 处理空输入
            if not first_line.strip():
                return ""
                
            user_input.append(first_line)
            
            # 检测代码块
            if first_line.startswith('```'):
                lang = first_line[3:].strip() or 'text'
                code_buffer = []
                
                while True:
                    line = self.session.prompt(
                        message=[('class:prompt', '... ')],
                        multiline=True
                    )
                    if line.strip() == '```':
                        break
                    code_buffer.append(line)
                
                # 代码高亮
                highlighted = self.highlight_code('\n'.join(code_buffer), lang)
                user_input.extend([f'```{lang}', highlighted, '```'])
                
            # 处理普通多行输入
            elif '\n' in first_line:  # 如果直接粘贴多行内容
                user_input = first_line.split('\n')
            else:  # 通过 Shift+Enter 输入的多行
                while True:
                    line = self.session.prompt(
                        message=[('class:prompt', '... ')],
                        multiline=True
                    )
                    if not line:  # 空行结束输入
                        break
                    user_input.append(line)
            
            return '\n'.join(user_input)
            
        except KeyboardInterrupt:
            return ""
        except EOFError:
            return "exit"

    def highlight_code(self, code: str, language: str) -> str:
        try:
            lexer = get_lexer_by_name(language)
            return highlight(code, lexer, Terminal256Formatter())
        except:
            return code

    def display_reasoning(self, content: str):
        self.console.print("\n[Reasoning Chain]", style="bold yellow")
        self.console.print(Panel.fit(content, border_style="yellow"))

    
    def display_message(self, content: str, style: str = None):
        """专业化的消息显示（自动处理代码块）"""
        if '```' in content:
            parts = content.split('```')
            for i, part in enumerate(parts):
                if i % 2 == 1:  # 代码块部分
                    lang, *code_lines = part.split('\n', 1)
                    code = code_lines[0] if code_lines else ""
                    print(self.highlight_code(code, lang.strip()))
                else:
                    print(part)
        else:
            print(content)
    def display_welcome(self, model: str):
        welcome_text = f"""
        DeepSeek Chat CLI (Model: {model})
        Enter 'q' or 'exit' or 'quit' to quit
        Commands:
         - /clear : Clear chat history
         - /save  : Save chat history
         - /load  : Load chat history
         - /help  : Show help
        Shortcuts:
         - Shift+Enter: New line
         - Up/Down: Navigate history
        """
        self.console.print(Panel.fit(welcome_text, title="Welcome", border_style="blue"))

class ConfigManager:
    def __init__(self):
        self.config_file = Path.home() / '.deepseek_config'
        self.default_config = {
            "api_key": "",
            "proxy": None
        }
    
    def save_config(self, api_key: str = None, proxy: str = None) -> None:
        """Save configuration to hidden file in user's home directory"""
        try:
            config = self.load_config()
            if api_key is not None:
                config["api_key"] = api_key
            if proxy is not None:
                config["proxy"] = proxy
                
            self.config_file.write_text(json.dumps(config))
            self.config_file.chmod(0o600)  # Set file permissions to owner read/write only
        except Exception as e:
            raise Exception(f"Failed to save config: {str(e)}")
    
    def load_config(self) -> dict:
        """Load configuration from hidden file"""
        try:
            if self.config_file.exists():
                config = json.loads(self.config_file.read_text())
                return {**self.default_config, **config}
            return self.default_config.copy()
        except Exception as e:
            raise Exception(f"Failed to load config: {str(e)}")
            
class ChatModel:
    def __init__(self, api_key: str, model: str = "deepseek-reasoner", proxy: str = None):
        if proxy and proxy.startswith('socks'):
            transport = SyncProxyTransport.from_url(proxy)
            http_client = httpx.Client(transport=transport)
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
                http_client=http_client
            )
        else:
            self.client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com"
            )
        self.model = model

    def get_response(self, messages: List[Dict]) -> Optional[str]:
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True
            )
        except Exception as e:
            raise Exception(f"API Error: {str(e)}")

class ChatHistory:
    def __init__(self):
        self.messages = [{"role": "system", "content": "You are a helpful assistant"}]
        self.reasoning_history = []
        
    def add_message(self, role: str, content: str, reasoning_content: str = None):
        self.messages.append({"role": role, "content": content})
        if reasoning_content:
            # Store reasoning chain without adding to messages to avoid API errors
            self.reasoning_history.append({"role": role, "reasoning_content": reasoning_content})
        
    def clear(self):
        self.messages = [{"role": "system", "content": "You are a helpful assistant"}]
        self.reasoning_history = []
        
    def save(self, filename: str):
        data = {
            "messages": self.messages,
            "reasoning_history": self.reasoning_history
        }
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
    def load(self, filename: str):
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.messages = data.get("messages", [])
                self.reasoning_history = data.get("reasoning_history", [])
                return True
        return False


class ChatApp:
    def __init__(self, api_key: Optional[str] = None, model: str = "deepseek-reasoner", proxy: str = None):
        self.config_manager = ConfigManager()
        config = self.config_manager.load_config()
        
        if api_key is None:
            api_key = config["api_key"]
            if not api_key:
                console = Console()
                api_key = Prompt.ask("Please enter your DeepSeek API key")
                self.config_manager.save_config(api_key=api_key)
                console.print("API key saved successfully!", style="green")
        else:
            self.config_manager.save_config(api_key=api_key)
            
        if proxy is None:
            proxy = config["proxy"]
        else:
            self.config_manager.save_config(proxy=proxy)
        
        self.model = ChatModel(api_key, model, proxy)
        self.history = ChatHistory()
        self.ui = ChatUI()
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def chat(self):
        self.ui.display_welcome(self.model.model)
        while True:
            try:
                user_input = self.ui.display_prompt()
                
                if user_input.lower() in ['q', 'quit', 'exit']:
                    user_choice = Prompt.ask("Are you sure you want to quit? (y/n)", default="n").strip().lower()
                    if user_choice == 'y':
                        self.ui.display_message("\nGoodbye!", style="yellow")
                        break
                    continue
                
                if not user_input:
                    continue
                    
                if self.handle_user_input(user_input):
                    continue
                
                self.history.add_message("user", user_input)
                
                response = self.model.get_response(self.history.messages)
                full_response = ""
                reasoning_content = ""
                
                # First, collect and display reasoning content
                for chunk in response:
                    if chunk.choices[0].delta.reasoning_content:
                        content = chunk.choices[0].delta.reasoning_content
                        reasoning_content += content
                        if len(reasoning_content) == len(content):  # First chunk
                            self.ui.display_message("\n[Reasoning Chain]", style="bold yellow")
                        self.ui.display_message(content, end="", flush=True)
                    elif chunk.choices[0].delta.content:
                        break
                
                if reasoning_content:
                    self.ui.display_message("\n")  # Add a newline after reasoning
                
                # Then display and collect the response
                self.ui.display_message("\nChat: ", style="bold blue", end="")
                
                for chunk in response:
                    if chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        full_response += content
                        self.ui.display_message(content, end="", flush=True)
                
                if full_response:
                    self.history.add_message("assistant", full_response, reasoning_content)
                    self.ui.display_message("")
                else:
                    self.ui.display_message("\nError: No response received", style="red")
                    
            except Exception as e:
                self.ui.display_message(f"\nError: {str(e)}", style="red")
                
    def _handle_interrupt(self, signum, frame):
        self.ui.display_message("\n\nSession terminated", style="yellow")
        exit(0)
        
    def handle_user_input(self, user_input: str) -> bool:
        if user_input.startswith("/"):
            cmd = user_input[1:]
            if cmd == "clear":
                self.history.clear()
                self.ui.display_message("Chat history cleared", style="yellow")
            elif cmd.startswith("save"):
                filename = cmd.split(maxsplit=1)[1] if len(cmd.split()) > 1 else "chat_history.json"
                self.history.save(filename)
                self.ui.display_message(f"Chat saved to {filename}", style="green")
            elif cmd.startswith("load"):
                filename = cmd.split(maxsplit=1)[1] if len(cmd.split()) > 1 else "chat_history.json"
                if self.history.load(filename):
                    self.ui.display_message("Chat history loaded", style="green")
                else:
                    self.ui.display_message(f"File not found: {filename}", style="red")
            elif cmd == "help":
                self.ui.display_welcome(self.model.model)
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description="DeepSeek Chat CLI")
    parser.add_argument("--api-key", help="DeepSeek API key (optional if already saved)")
    parser.add_argument("--model", default="deepseek-reasoner", help="Model to use")
    parser.add_argument("--proxy", help="Proxy server address (e.g., socks5://127.0.0.1:7890)")
    args = parser.parse_args()
    
    app = ChatApp(api_key=args.api_key, model=args.model, proxy=args.proxy)
    app.chat()

if __name__ == "__main__":
    main()
