#:package StackExchange.Redis@3.4.35
#:package Soenneker.Flywheel.Redis@11.0.567
#:package Microsoft.AspNetCore.DataProtection.StackExchangeRedis@11.0.0
#:property PublishAot=false

using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Xml.Linq;
using Microsoft.AspNetCore.DataProtection.StackExchangeRedis;
using Soenneker.Flywheel.Communication.Dtos;
using Soenneker.Flywheel.Communication.Requests;
using Soenneker.Flywheel.Redis;
using StackExchange.Redis;

string port = Environment.GetEnvironmentVariable("TEST_REDIS_PORT") ?? throw new InvalidOperationException("Run test/cache.py");
var options = ConfigurationOptions.Parse($"127.0.0.1:{port},ssl=true,sslHost=localhost,abortConnect=false,connectTimeout=30000");
// Send admin commands so the test verifies the server-side ACL.
options.AllowAdmin = true;
options.Password = Environment.GetEnvironmentVariable("TEST_REDIS_PASSWORD");
using var certificate = X509Certificate2.CreateFromPem(Environment.GetEnvironmentVariable("TEST_TLS_CERT"));
options.CertificateValidation += (_, presented, _, errors) =>
    presented?.Equals(certificate) == true && (errors & ~SslPolicyErrors.RemoteCertificateChainErrors) == 0;

using var redis = await ConnectionMultiplexer.ConnectAsync(options);
var db = redis.GetDatabase();
var keys = new RedisXmlRepository(() => db, "data-protection");
await using var jobs = new RedisJobStore(_ => Task.FromResult(db), "container-test");
if (await db.KeyExistsAsync("dotnet"))
    throw new InvalidOperationException("Expected an empty cache on startup.");

keys.StoreElement(new XElement("test", "api-key-ring-test"), "test");
var request = new EnqueueRequest("test.v1", "{}", new JobPolicy(), TimeSpan.Zero);
await db.StringSetAsync("expected-job", await jobs.Enqueue(request, CancellationToken.None));
await db.StringSetAsync("dotnet", "cached");
if (await db.StringGetAsync("dotnet") != "cached")
    throw new InvalidOperationException("String round trip failed.");

if (!await db.LockTakeAsync("lock", "owner", TimeSpan.FromSeconds(30)) || !await db.LockReleaseAsync("lock", "owner"))
    throw new InvalidOperationException("Distributed lock acquisition or release failed.");

var result = await db.ScriptEvaluateAsync("redis.call('HSET', KEYS[1], 'job', 'queued'); return redis.call('ZADD', KEYS[2], 1, 'job')",
    [new RedisKey("jobs"), new RedisKey("queue")]);
if ((long)result != 1 || await db.HashGetAsync("jobs", "job") != "queued")
    throw new InvalidOperationException("Job queue operations failed.");

// A script's read and write must be atomic across independent client connections.
const int clients = 8, increments = 80;
const string counter = "lua-atomic-counter";
const string increment = """
    local n = tonumber(redis.call('GET', KEYS[1]))
    -- Widen the overlap between concurrent scripts to exercise the read/modify/write race.
    for i = 1, 40 do redis.call('GET', KEYS[1]) end
    return redis.call('SET', KEYS[1], n + 1)
    """;
await db.StringSetAsync(counter, 0);
await Task.WhenAll(Enumerable.Range(0, clients).Select(async _ =>
{
    using var client = await ConnectionMultiplexer.ConnectAsync(options);
    IDatabase worker = client.GetDatabase();
    for (int i = 0; i < increments; i++)
        await worker.ScriptEvaluateAsync(increment, [new RedisKey(counter)]);
}));
long actual = (long)await db.StringGetAsync(counter);
if (actual != clients * increments)
    throw new InvalidOperationException($"Concurrent Lua updates lost: expected {clients * increments}, found {actual}.");

if (!keys.GetAllElements().Any(element => element.Value == "api-key-ring-test"))
    throw new InvalidOperationException("The API data protection record was unreadable.");
var claim = await jobs.Claim("engine", TimeSpan.FromMinutes(1), CancellationToken.None);
if (claim is null || claim.Job.Id != (string?)await db.StringGetAsync("expected-job"))
    throw new InvalidOperationException("The Engine could not claim the queued Flywheel job.");

foreach (var command in new[] { "SAVE", "BGSAVE", "FLUSHALL" })
{
    try
    {
        await db.ExecuteAsync(command);
        throw new InvalidOperationException($"Application user could execute {command}.");
    }
    catch (RedisServerException error) when (error.Message.Contains("NOPERM", StringComparison.OrdinalIgnoreCase)) { }
}

await db.StringSetAsync("ttl", "temporary", TimeSpan.FromMinutes(1));
if (await db.KeyTimeToLiveAsync("ttl") is not { TotalSeconds: > 0 })
    throw new InvalidOperationException("Cache TTL was missing.");
