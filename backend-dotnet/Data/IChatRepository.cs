namespace backend_dotnet.Data;

public sealed record AcceptedTurn(Guid ConversationId, Guid TurnId, int SequenceNumber, string Status, bool IsNew);

public interface IChatRepository
{
    // Null means a conversationId was given but doesn't exist, or isn't owned
    // by userId -- the caller must not reveal which. A duplicate
    // (user_id, idempotency_key) returns the existing turn (IsNew: false)
    // and never creates a conversation row, even when none was supplied.
    Task<AcceptedTurn?> AcceptUserMessageAsync(
        Guid userId, Guid? conversationId, string text, string idempotencyKey, CancellationToken cancellationToken);
}
