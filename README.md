# Soenneker.Cloudflare.Garnet

Run Microsoft Garnet 2.2.13 as a shared, in-memory cache on Cloudflare Containers. The default deployment works without Spectrum. A loopback relay lets Redis-compatible clients use the Worker's authenticated WebSocket endpoint. A Durable Object routes every connection to the same Garnet instance.

## Deploy the cache

1. Enable Containers on a Workers Paid account and enable its `workers.dev` subdomain. Install Docker, Node.js 24 or newer, and the repo dependencies with `npm ci`. Authenticate Wrangler to that account.
2. Set these four Worker secrets with `npx wrangler secret put <NAME>`. Enter PEMs with their original line breaks.

   | Secret | Purpose |
   | --- | --- |
   | `GARNET_PASSWORD` | Password used by the Garnet client. |
   | `TLS_CERT_PEM` | Full certificate chain Garnet presents to its clients. Choose a certificate name such as `garnet.internal.example.com` and trust its issuer on each client. |
   | `TLS_KEY_PEM` | Private key matching the Garnet certificate. |
   | `GARNET_RELAY_TOKEN` | Independent, high-entropy bearer token shared by the Worker and authorized relays. Generate one with `openssl rand -hex 32`. |

3. Run `npm run deploy`. Take the HTTPS Worker URL printed by Wrangler, change the scheme to `wss`, and append `/garnet`. For example, `wss://soenneker-cloudflare-garnet.<account-subdomain>.workers.dev/garnet`.

## Connect without Spectrum

Run the supplied relay in the same network namespace as each application that needs the cache. For separate API and Engine containers, run one relay alongside each process. All relays connect to the same cache. The Worker authenticates the relay token on each WebSocket upgrade. Garnet authenticates the client on a separate TLS connection carried through the relay.

1. Set `GARNET_RELAY_URL` to the full `wss://…/garnet` URL and inject `GARNET_RELAY_TOKEN` into the relay's environment from your secret store.
2. From the repo root, run `dotnet run --file relay/GarnetRelay.cs` with the .NET 10 or newer SDK. To package it for a Linux container, run `dotnet publish relay/GarnetRelay.cs -c Release -r linux-x64 --self-contained true -o ./out/relay`, copy the output into the application image, and start `GarnetRelay` alongside the application. The relay listens on `127.0.0.1:6380`. Set `GARNET_RELAY_PORT` to choose another port.
3. Give StackExchange.Redis the loopback endpoint and the name on the **Garnet certificate**. The example certificate name is used for TLS validation even though the TCP connection goes to loopback.

```text
127.0.0.1:6380,ssl=true,sslHost=garnet.internal.example.com,password=<garnet-password>,abortConnect=false,connectTimeout=30000
```

The example allows 30 seconds for a cold connection. An existing StackExchange.Redis multiplexer can reconnect through the same loopback port after a relay restart. Commands in flight during a disconnect can fail.

## Optional direct TCP access

Use Spectrum if clients need to connect directly to a public Garnet hostname. Configure a Spectrum TCP application on an eligible zone with the Worker as origin and TLS passthrough. Use a certificate valid for the public Garnet hostname. Direct clients can then connect with `garnet.example.com:6379,ssl=true,password=<garnet-password>,abortConnect=false`. For a direct-TCP-only deployment, set `workers_dev` to `false` and omit the relay token. [Cloudflare Spectrum setup](https://developers.cloudflare.com/spectrum/how-to/route-tcp-traffic-to-a-worker/)

## Cache lifecycle

A five-minute cron keeps the instance warm and checks for image changes. The update takes effect when Cloudflare exposes the new image digest to the Durable Object and its next maintenance check runs. An image upload alone can leave the previous instance serving. On startup the Worker waits for Garnet to complete a TLS handshake before forwarding client traffic. Restarts and image replacements disconnect clients and start with an empty dataset. Use this endpoint for data the application can rebuild on a miss. Keep queued jobs, shared encryption keys, and coordination state on a service with the durability they require. [Cloudflare container lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/)

The default instance has 4 GiB of RAM. `GARNET_MEMORY_MIB` defaults to 512 MiB for Garnet's main log and the hash index starts at 16 MiB. Set the variable in Wrangler to tune the next instance. Leave room for the runtime, index, and client connections. Garnet manages memory through its own log and index settings. [Garnet memory configuration](https://microsoft.github.io/garnet/docs/getting-started/memory)

## Redis client compatibility

The image pins Garnet 2.2.13 by digest and terminates TLS in Garnet. The application ACL supports common data commands and Lua. Tests exercise StackExchange.Redis strings and TTLs, locks, hashes, sorted sets, and scripts. They also exercise an ASP.NET data-protection record and a Flywheel enqueue and claim within one running instance. The ACL denies administrative operations including `SAVE`, `BGSAVE`, and `FLUSHALL`.

**Redis Streams:** the tested image returns an unknown-command error for `XADD`. Workloads that depend on Streams require a different backend. [Garnet command compatibility](https://microsoft.github.io/garnet/docs/welcome/compatibility)

## Verify locally

With Docker, Python 3, OpenSSL, and the .NET 11 SDK installed, run `python test/cache.py` to check StackExchange.Redis over TLS and cold starts after graceful shutdown and forced termination. Run `node --test test/image-update.test.js` to check image replacement and `npx wrangler deploy --dry-run` to validate the deployment bundle.
