namespace backend_dotnet.Data;

public sealed record JournalEntryRecord(Guid Id, string Body, string? Mood, DateTimeOffset CreatedAt, DateTimeOffset UpdatedAt);

public interface IJournalRepository
{
    // Idempotent on (userId, idempotencyKey): a duplicate POST returns the
    // existing row rather than creating a second one, and never enqueues a
    // second extraction job for it.
    Task<JournalEntryRecord> CreateAsync(
        Guid userId, string body, string? mood, string idempotencyKey, CancellationToken cancellationToken);

    Task<IReadOnlyList<JournalEntryRecord>> ListAsync(Guid userId, int limit, int offset, CancellationToken cancellationToken);

    // Null means the entry doesn't exist or isn't owned by userId -- the
    // caller must not reveal which (same convention as the chat/memory repos).
    Task<JournalEntryRecord?> GetAsync(Guid userId, Guid id, CancellationToken cancellationToken);

    Task<JournalEntryRecord?> UpdateAsync(
        Guid userId, Guid id, string body, string? mood, CancellationToken cancellationToken);

    Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken cancellationToken);
}
