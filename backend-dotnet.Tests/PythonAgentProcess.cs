using System.Diagnostics;
using System.Net;
using System.Net.Sockets;

namespace backend_dotnet.Tests;

// Spawns the real agent-python FastAPI app as a subprocess so integration
// tests exercise the actual trust boundary (.NET HTTP -> Python HTTP), not a
// stub. Task said "real local Postgres + both services" -- this is both.
public sealed class PythonAgentProcess : IAsyncDisposable
{
    private readonly Process _process;

    public int Port { get; }
    public string BaseUrl => $"http://127.0.0.1:{Port}";

    private PythonAgentProcess(Process process, int port)
    {
        _process = process;
        Port = port;
    }

    public static async Task<PythonAgentProcess> StartAsync(
        string databaseUrl, string signingKey, string issuer, string audience)
    {
        var agentDir = Path.Combine(FindRepoRoot(), "agent-python");
        var uvicornExe = Path.Combine(agentDir, ".venv", "bin", "uvicorn");
        var port = GetFreeTcpPort();

        var startInfo = new ProcessStartInfo
        {
            FileName = uvicornExe,
            WorkingDirectory = agentDir,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
        };
        startInfo.ArgumentList.Add("main:app");
        startInfo.ArgumentList.Add("--port");
        startInfo.ArgumentList.Add(port.ToString());
        startInfo.Environment["DATABASE_URL"] = databaseUrl;
        startInfo.Environment["INTERNAL_SIGNING_KEY"] = signingKey;
        startInfo.Environment["INTERNAL_TOKEN_ISSUER"] = issuer;
        startInfo.Environment["INTERNAL_TOKEN_AUDIENCE"] = audience;
        startInfo.Environment["GROQ_API_KEY"] = "";

        var process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Failed to start the Python agent process");

        var agent = new PythonAgentProcess(process, port);
        await agent.WaitUntilHealthyAsync();
        return agent;
    }

    private async Task WaitUntilHealthyAsync()
    {
        using var client = new HttpClient();
        var deadline = DateTime.UtcNow.AddSeconds(20);
        Exception? lastError = null;

        while (DateTime.UtcNow < deadline)
        {
            if (_process.HasExited)
            {
                var stderr = await _process.StandardError.ReadToEndAsync();
                throw new InvalidOperationException($"Python agent exited early (code {_process.ExitCode}):\n{stderr}");
            }

            try
            {
                var response = await client.GetAsync($"{BaseUrl}/health");
                if (response.IsSuccessStatusCode)
                {
                    return;
                }
            }
            catch (Exception ex)
            {
                lastError = ex;
            }

            await Task.Delay(200);
        }

        throw new TimeoutException($"Python agent did not become healthy in time. Last error: {lastError}");
    }

    private static int GetFreeTcpPort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private static string FindRepoRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "PLAN.md")))
        {
            directory = directory.Parent;
        }
        if (directory is null)
        {
            throw new InvalidOperationException($"Could not locate repo root (PLAN.md) from {AppContext.BaseDirectory}");
        }
        return directory.FullName;
    }

    public async ValueTask DisposeAsync()
    {
        if (!_process.HasExited)
        {
            _process.Kill(entireProcessTree: true);
            try
            {
                await _process.WaitForExitAsync();
            }
            catch
            {
                // best-effort cleanup
            }
        }
        _process.Dispose();
    }
}
