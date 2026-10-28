namespace backend_dotnet.Data;

public sealed record AcceptedTurn(Guid TurnId, int SequenceNumber, string Status, bool IsNew);

public interface IChatRepository
{
    // Null means conversationId was given but doesn't exist, or isn't owned
    // by userId -- the caller must not reveal which.
    Task<Guid?> EnsureConversationAsync(Guid userId, Guid? conversationId, CancellationToken cancellationToken);

    Task<AcceptedTurn> AcceptUserMessageAsync(
        Guid userId, Guid conversationId, string text, string idempotencyKey, CancellationToken cancellationToken);
}
