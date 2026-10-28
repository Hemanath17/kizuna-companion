using backend_dotnet.Data;

namespace backend_dotnet.Tests;

// Replaces the real (Postgres-backed) repository for tests that only care
// about the auth layer, so they don't need a database.
public sealed class RecordingProfileRepository : IProfileRepository
{
    public List<(Guid UserId, string? Email)> Calls { get; } = [];

    public Task EnsureProfileAsync(Guid userId, string? email, CancellationToken cancellationToken)
    {
        Calls.Add((userId, email));
        return Task.CompletedTask;
    }
}
