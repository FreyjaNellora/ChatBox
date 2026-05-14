import { ChildProcess } from 'child_process';

/**
 * MCP Bridge — handles JSON-RPC protocol to the Python broker.
 *
 * The broker speaks MCP over stdio. This bridge crafts proper
 * MCP messages and parses responses. Requests are queued to prevent
 * concurrent stdin interleaving.
 */

interface McpRequest {
    jsonrpc: '2.0';
    id: number;
    method: string;
    params: any;
}

interface McpResponse {
    jsonrpc: '2.0';
    id: number;
    result?: {
        content: Array<{ type: string; text: string }>;
    };
    error?: any;
}

let _idCounter = 0;

function nextId(): number {
    return ++_idCounter;
}

// Queue for serializing MCP requests over stdio
interface PendingRequest {
    id: number;
    resolve: (value: any) => void;
    reject: (reason: any) => void;
    timer: ReturnType<typeof setTimeout>;
}

const pending = new Map<number, PendingRequest>();
let queue: Array<() => void> = [];
let isProcessing = false;

function enqueue(fn: () => void) {
    queue.push(fn);
    if (!isProcessing) {
        processQueue();
    }
}

function processQueue() {
    if (queue.length === 0) {
        isProcessing = false;
        return;
    }
    isProcessing = true;
    const fn = queue.shift()!;
    fn();
    // Give event loop a tick before next — responses are async
    setTimeout(processQueue, 0);
}

function setupBrokerListener(broker: ChildProcess) {
    broker.stdout?.on('data', (data: Buffer) => {
        const lines = data.toString().split('\n').filter(l => l.trim());
        for (const line of lines) {
            try {
                const resp: McpResponse = JSON.parse(line);
                const req = pending.get(resp.id);
                if (req) {
                    pending.delete(resp.id);
                    clearTimeout(req.timer);
                    if (resp.error) {
                        req.reject(resp.error);
                    } else if (resp.result?.content?.[0]?.text) {
                        req.resolve(JSON.parse(resp.result.content[0].text));
                    } else {
                        req.resolve(resp.result);
                    }
                }
            } catch {
                // Non-JSON line — ignore or log
            }
        }
    });
}

export function callTool(
    broker: ChildProcess,
    toolName: string,
    args: any
): Promise<any> {
    return new Promise((resolve, reject) => {
        enqueue(() => {
            const id = nextId();
            const req: McpRequest = {
                jsonrpc: '2.0',
                id,
                method: 'tools/call',
                params: { name: toolName, arguments: args }
            };

            const timer = setTimeout(() => {
                pending.delete(id);
                reject(new Error(`MCP call timeout: ${toolName}`));
            }, 10000);

            pending.set(id, { id, resolve, reject, timer });
            broker.stdin?.write(JSON.stringify(req) + '\n');
        });
    });
}

/**
 * Start a persistent listen loop that polls the broker and
 * forwards messages to a callback.
 */
export async function startListenLoop(
    broker: ChildProcess,
    agentName: string,
    channels: string[],
    view: string,
    onMessages: (msgs: any[]) => void
): Promise<() => void> {
    let sinceId = 0;
    let running = true;

    const loop = async () => {
        while (running) {
            try {
                const result = await callTool(broker, 'listen', {
                    agent_name: agentName,
                    channels,
                    view,
                    since_id: sinceId,
                    timeout_ms: 1500,
                    max_msgs: 10
                });

                if (result.status === 'ok' && result.messages && result.messages.length > 0) {
                    const msgs = result.messages;
                    sinceId = msgs[msgs.length - 1].id;
                    onMessages(msgs);
                }
            } catch (err) {
                console.error('[AgentChat ListenLoop]', err);
                await sleep(5000);
            }
        }
    };

    loop();

    return () => { running = false; };
}

function sleep(ms: number): Promise<void> {
    return new Promise(r => setTimeout(r, ms));
}

// Auto-setup listener on first callTool if broker.stdout is available
const _originalCallTool = callTool;
let listenerSetup = false;
export function callToolSafe(broker: ChildProcess, toolName: string, args: any): Promise<any> {
    if (!listenerSetup && broker.stdout) {
        setupBrokerListener(broker);
        listenerSetup = true;
    }
    return _originalCallTool(broker, toolName, args);
}
