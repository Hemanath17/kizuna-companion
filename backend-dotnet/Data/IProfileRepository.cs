namespace backend_dotnet.Data;

public interface IProfileRepository
{
    Task EnsureProfileAsync(Guid userId, string? email, CancellationToken cancellationToken);
}
