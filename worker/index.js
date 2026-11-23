import { DurableObject } from "cloudflare:workers";
import { replaceIfNeeded } from "./image-update.js";
import { upgradeGarnet } from "./garnet-websocket.js";

const GARNET_PORT = 6379;
const IDLE_MS = 30 * 60 * 1000;
const socketOptions = { allowHalfOpen: true, secureTransport: "off" };

async function forward(client, upstream) {
    try {
        await upstream.opened;
        await Promise.all([
            client.readable.pipeTo(upstream.writable),
            upstream.readable.pipeTo(client.writable),
        ]);
    } finally {
        await Promise.allSettled([client.close(), upstream.close()]);
    }
}

export class GarnetHost extends DurableObject {
    start() {
        const container = this.ctx.container;
        if (!container.running) {
            this.ready = undefined;
            const env = {};
            for (const key of ["GARNET_PASSWORD", "TLS_CERT_PEM", "TLS_KEY_PEM", "GARNET_MEMORY_MIB"]) {
                if (this.env[key]) env[key] = this.env[key];
            }
            container.start({ image: container.images.garnet, instance: "standard-1", enableInternet: false, env });
        }
        return container.setInactivityTimeout(IDLE_MS);
    }

    waitForTls() {
        this.ready ??= this.checkTls().catch(error => {
            this.ready = undefined;
            throw error;
        });
        return this.ready;
    }

    async checkTls() {
        // Cloudflare can accept a forwarded socket before Garnet binds its TLS listener.
        const probe = await this.ctx.container.exec(["/bin/sh", "/usr/local/bin/wait-garnet.sh"]);
        if ((await probe.output()).exitCode !== 0) throw new Error("Garnet TLS startup timed out");
    }

    async openGarnetSocket() {
        const container = this.ctx.container;
        const port = container.getTcpPort(GARNET_PORT);
        let lastError;
        for (let attempt = 0; attempt < 240 && container.running; attempt++) {
            let upstream;
            try {
                upstream = port.connect("10.0.0.1:6379", socketOptions);
                await upstream.opened;
                return upstream;
            } catch (error) {
                lastError = error;
                if (upstream) await upstream.close().catch(() => {});
                await scheduler.wait(500);
            }
        }
        throw new Error("Garnet did not accept a connection", { cause: lastError });
    }

    async connect(socket) {
        try {
            if (this.updating) await this.updating;
            await this.start();
            await this.waitForTls();
            const upstream = await this.openGarnetSocket();
            await forward(socket, upstream);
        } catch (error) {
            await socket.close().catch(() => {});
            throw error;
        }
    }

    maintain() {
        this.updating ??= this.reconcileImage().finally(() => { this.updating = undefined; });
        return this.updating;
    }

    async reconcileImage() {
        const container = this.ctx.container;
        await replaceIfNeeded(container);
        await this.start();
        await this.waitForTls();
    }
}

export default {
    async connect(socket, env) {
        const host = env.GARNET_HOST.getByName("primary");
        await forward(socket, host.connect("garnet:6379", socketOptions));
    },
    async scheduled(_event, env) {
        await env.GARNET_HOST.getByName("primary").maintain();
    },
    fetch: upgradeGarnet,
};
