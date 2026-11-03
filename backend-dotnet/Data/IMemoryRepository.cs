namespace backend_dotnet.Data;

public sealed record MemoryRecord(
    Guid Id,
    string MemoryType,
    string Content,
    double? Confidence,
    string Status,
    bool UserVerified,
    bool IsSensitive,
    bool PendingConfirmation,
    string SourceKind,
    Guid? SupersededBy,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt);

public interface IMemoryRepository
{
    // Excludes status='deleted': a forgotten fact never reappears here either.
    Task<IReadOnlyList<MemoryRecord>> ListAsync(Guid userId, CancellationToken cancellationToken);

    // Null means the memory doesn't exist or isn't owned by userId -- the
    // caller must not reveal which (same convention as AcceptUserMessageAsync).
    Task<MemoryRecord?> CorrectOrApproveAsync(
        Guid userId, Guid memoryId, string? content, CancellationToken cancellationToken);

    // False means the memory doesn't exist, isn't owned by userId, or was
    // already forgotten.
    Task<bool> ForgetAsync(Guid userId, Guid memoryId, CancellationToken cancellationToken);
}
