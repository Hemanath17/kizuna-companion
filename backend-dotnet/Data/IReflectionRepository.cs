namespace backend_dotnet.Data;

public sealed record ReflectionRecord(
    Guid Id,
    DateTimeOffset PeriodStart,
    DateTimeOffset PeriodEnd,
    string? Body,
    string Status,
    string? RejectionReason,
    DateTimeOffset CreatedAt,
    DateTimeOffset? GeneratedAt);

public sealed record ReflectionNoteRecord(Guid Id, string Body, DateTimeOffset CreatedAt);

public interface IReflectionRepository
{
    // Idempotent on (userId, periodStart, periodEnd): a repeat request for
    // the same window returns the existing row rather than generating a
    // duplicate reflection_generate job.
    Task<ReflectionRecord> CreateOrGetAsync(
        Guid userId, DateTimeOffset periodStart, DateTimeOffset periodEnd, CancellationToken cancellationToken);

    // Excludes status='rejected': a rejected reflection is never visible
    // through any endpoint (PLAN.md Phase 4 decision 8).
    Task<IReadOnlyList<ReflectionRecord>> ListAsync(Guid userId, CancellationToken cancellationToken);

    // Null means not found, not owned by userId, or rejected -- the caller
    // must not reveal which.
    Task<ReflectionRecord?> GetAsync(Guid userId, Guid id, CancellationToken cancellationToken);

    Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken cancellationToken);

    // False if the reflection doesn't exist, isn't owned by userId, or is
    // rejected -- same "can't comment on what you can't see" rule as GetAsync.
    Task<bool> AddNoteAsync(Guid userId, Guid reflectionId, string body, CancellationToken cancellationToken);

    // Null means the reflection doesn't exist, isn't owned by userId, or is
    // rejected (GET endpoint turns this into a 404) -- not in the Step 4.3
    // prompt's enumerated endpoint list, but a note composer with no way to
    // read back what it saved isn't a working feature, so it's added here.
    Task<IReadOnlyList<ReflectionNoteRecord>?> ListNotesAsync(Guid userId, Guid reflectionId, CancellationToken cancellationToken);
}
