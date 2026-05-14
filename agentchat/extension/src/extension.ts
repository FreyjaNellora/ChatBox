import * as vscode from 'vscode';
import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import { callToolSafe as callTool, startListenLoop } from './mcp-bridge';

let brokerProcess: ChildProcess | undefined;
let stopListenLoop: (() => void) | undefined;

export function activate(context: vscode.ExtensionContext) {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!workspaceRoot) {
        vscode.window.showWarningMessage('AgentChat: no workspace folder open');
        return;
    }

    const brokerPath = path.join(context.extensionPath, '..', 'broker.py');
    
    // Spawn Python broker as child process
    brokerProcess = spawn('python', [brokerPath], {
        cwd: workspaceRoot,
        env: {
            ...process.env,
            AGENTCHAT_WORKSPACE: workspaceRoot
        },
        stdio: ['pipe', 'pipe', 'pipe']
    });

    brokerProcess.stderr?.on('data', (data) => {
        console.error(`[AgentChat Broker] ${data}`);
    });

    brokerProcess.on('exit', (code) => {
        console.log(`[AgentChat Broker] exited with code ${code}`);
    });

    // Register webview provider
    const provider = new AgentChatProvider(context.extensionUri, brokerProcess, workspaceRoot);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('agentchat.panel', provider)
    );

    // Register open command
    context.subscriptions.push(
        vscode.commands.registerCommand('agentchat.open', () => {
            vscode.commands.executeCommand('agentchat.panel.focus');
        })
    );
}

export function deactivate() {
    if (stopListenLoop) {
        stopListenLoop();
    }
    if (brokerProcess && !brokerProcess.killed) {
        brokerProcess.kill();
    }
}

class AgentChatProvider implements vscode.WebviewViewProvider {
    private _view?: vscode.WebviewView;
    private _broker: ChildProcess;
    private _workspaceRoot: string;
    private _channels: any[] = [];
    private _currentChannel = '#general';
    private _messages: any[] = [];

    constructor(
        private readonly _extensionUri: vscode.Uri,
        broker: ChildProcess,
        workspaceRoot: string
    ) {
        this._broker = broker;
        this._workspaceRoot = workspaceRoot;
    }

    async resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [this._extensionUri]
        };
        webviewView.webview.html = this._getHtml();

        // Resolve user name once for this webview session
        const userName = vscode.workspace.getConfiguration('agentchat').get('userName', 'nate');

        // Handle messages from webview
        webviewView.webview.onDidReceiveMessage(async (message) => {
            if (message.type === 'send') {
                try {
                    await callTool(this._broker, 'chat', {
                        agent_name: userName,
                        channel: message.channel,
                        body: message.body
                    });
                } catch (err) {
                    console.error('[AgentChat] Send failed:', err);
                }
            } else if (message.type === 'switchChannel') {
                this._currentChannel = message.channel;
                await this._refreshMessages();
            } else if (message.type === 'getPost') {
                try {
                    const post = await callTool(this._broker, 'get_post', {
                        post_id: message.postId
                    });
                    webviewView.webview.postMessage({ type: 'postDetail', data: post });
                } catch (err) {
                    console.error('[AgentChat] get_post failed:', err);
                }
            }
        });

        // Load initial channel list
        await this._refreshChannels();

        // Register user with broker before starting listen loop
        const allChannels = this._channels.map((c: any) => c.name);
        if (allChannels.length > 0) {
            try {
                await callTool(this._broker, 'hello', {
                    name: userName,
                    phase: '*',
                    default_channels: allChannels
                });
            } catch (err) {
                console.error('[AgentChat] hello() failed:', err);
            }
        }

        // Start listen loop for all subscribed channels
        if (allChannels.length > 0 && brokerProcess) {
            stopListenLoop = await startListenLoop(
                brokerProcess,
                userName,
                allChannels,
                'full',
                (msgs) => {
                    this._messages.push(...msgs);
                    this._renderMessages();
                }
            );
        }
    }

    private async _refreshChannels() {
        try {
            const result = await callTool(this._broker, 'rooms', {});
            if (result.status === 'ok' && result.channels) {
                this._channels = result.channels;
                this._view?.webview.postMessage({ type: 'channels', data: result.channels });
            }
        } catch (err) {
            console.error('[AgentChat] rooms() failed:', err);
        }
    }

    private async _refreshMessages() {
        // Fetch recent messages for current channel
        const userName = vscode.workspace.getConfiguration('agentchat').get('userName', 'nate');
        try {
            const result = await callTool(this._broker, 'listen', {
                agent_name: userName,
                channels: [this._currentChannel],
                view: 'full',
                since_id: 0,
                timeout_ms: 1000,
                max_msgs: 50
            });
            if (result.status === 'ok' && result.messages) {
                this._messages = result.messages;
                this._renderMessages();
            }
        } catch (err) {
            console.error('[AgentChat] refresh messages failed:', err);
        }
    }

    private _renderMessages() {
        const channelMsgs = this._messages.filter(m => m.channel === this._currentChannel);
        this._view?.webview.postMessage({ type: 'messages', data: channelMsgs });
    }

    private _getHtml(): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AgentChat</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--vscode-font-size);
            color: var(--vscode-foreground);
            background: var(--vscode-editor-background);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        #container { display: flex; flex: 1; overflow: hidden; }
        #channel-list {
            width: 200px;
            border-right: 1px solid var(--vscode-panel-border);
            padding: 8px;
            overflow-y: auto;
            flex-shrink: 0;
        }
        .channel-item {
            padding: 6px 8px;
            cursor: pointer;
            border-radius: 4px;
            margin-bottom: 2px;
        }
        .channel-item:hover { background: var(--vscode-list-hoverBackground); }
        .channel-item.active { background: var(--vscode-list-activeSelectionBackground); }
        .channel-name { font-weight: 600; }
        .channel-desc { font-size: 0.85em; opacity: 0.7; }
        #main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
        #transcript { flex: 1; padding: 12px; overflow-y: auto; }
        .message { margin-bottom: 12px; padding: 8px; border-radius: 6px; background: var(--vscode-editor-inactiveSelectionBackground); }
        .message-header { font-size: 0.85em; opacity: 0.7; margin-bottom: 4px; }
        .message-body { line-height: 1.4; }
        .post-card { border-left: 3px solid var(--vscode-focusBorder); padding-left: 10px; }
        .post-title { font-weight: 600; margin-bottom: 4px; }
        .post-desc { opacity: 0.85; }
        .reply { margin-left: 20px; border-left: 2px solid var(--vscode-panel-border); padding-left: 8px; }
        #composer {
            border-top: 1px solid var(--vscode-panel-border);
            padding: 8px;
            display: flex;
            gap: 8px;
        }
        #composer input {
            flex: 1;
            padding: 6px 10px;
            border: 1px solid var(--vscode-input-border);
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border-radius: 4px;
        }
        #composer button {
            padding: 6px 16px;
            border: none;
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border-radius: 4px;
            cursor: pointer;
        }
        #composer button:hover { background: var(--vscode-button-hoverBackground); }
    </style>
</head>
<body>
    <div id="container">
        <div id="channel-list"></div>
        <div id="main">
            <div id="transcript"></div>
            <div id="composer">
                <input type="text" id="msg-input" placeholder="Message #general..." />
                <button id="send-btn">Send</button>
            </div>
        </div>
    </div>
    <script>
        const vscode = acquireVsCodeApi();
        let currentChannel = '#general';
        let channels = [];
        let messages = [];

        function renderChannels() {
            const list = document.getElementById('channel-list');
            list.innerHTML = channels.map(ch => \`
                <div class="channel-item \${ch.name === currentChannel ? 'active' : ''}" data-name="\${ch.name}">
                    <div class="channel-name">\${ch.name}</div>
                    <div class="channel-desc">\${ch.description}</div>
                </div>
            \`).join('');
            list.querySelectorAll('.channel-item').forEach(el => {
                el.addEventListener('click', () => {
                    currentChannel = el.dataset.name;
                    vscode.postMessage({ type: 'switchChannel', channel: currentChannel });
                    renderChannels();
                });
            });
        }

        function renderTranscript() {
            const tx = document.getElementById('transcript');
            const channelMsgs = messages.filter(m => m.channel === currentChannel);
            tx.innerHTML = channelMsgs.map(m => {
                if (m.kind === 'post') {
                    return \`
                        <div class="message post-card" data-post-id="\${m.id}">
                            <div class="message-header">\${m.author} (\${m.phase}) • \${new Date(m.ts * 1000).toLocaleTimeString()}</div>
                            <div class="post-title">\${m.title || ''}</div>
                            <div class="post-desc">\${m.description || ''}</div>
                            <div style="font-size:0.8em; opacity:0.6; margin-top:4px;">\${m.reply_count || 0} replies</div>
                        </div>
                    \`;
                } else if (m.kind === 'reply') {
                    return \`
                        <div class="message reply">
                            <div class="message-header">\${m.author} (\${m.phase})</div>
                            <div class="message-body">\${m.body}</div>
                        </div>
                    \`;
                } else {
                    return \`
                        <div class="message">
                            <div class="message-header">\${m.author} (\${m.phase}) • \${new Date(m.ts * 1000).toLocaleTimeString()}</div>
                            <div class="message-body">\${m.body}</div>
                        </div>
                    \`;
                }
            }).join('');
            tx.scrollTop = tx.scrollHeight;

            // Click on post to view replies
            tx.querySelectorAll('.post-card').forEach(el => {
                el.addEventListener('click', () => {
                    const postId = parseInt(el.dataset.postId);
                    vscode.postMessage({ type: 'getPost', postId });
                });
            });
        }

        document.getElementById('send-btn').addEventListener('click', () => {
            const input = document.getElementById('msg-input');
            const body = input.value.trim();
            if (!body) return;
            vscode.postMessage({ type: 'send', channel: currentChannel, body });
            input.value = '';
        });

        document.getElementById('msg-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') document.getElementById('send-btn').click();
        });

        window.addEventListener('message', (e) => {
            const msg = e.data;
            if (msg.type === 'channels') {
                channels = msg.data;
                renderChannels();
            } else if (msg.type === 'messages') {
                messages = msg.data;
                renderTranscript();
            } else if (msg.type === 'postDetail') {
                const post = msg.data.post;
                const replies = msg.data.replies;
                alert(\`Post: \${post.title || post.body}\\n\\nReplies:\\n\${replies.map(r => '- ' + r.body).join('\\n')}\`);
            }
        });

        // Request channels on load
        renderChannels();
    </script>
</body>
</html>`;
    }
}
