using Npgsql;

namespace backend_dotnet.Data;

public static class PostgresConnectionString {
    // Supabase hands out a postgres:// URI; Npgsql only accepts its own
    // keyword=value format, so convert.
    public static string FromUrl(string databaseUrl)
    {
        var uri = new Uri(databaseUrl);
        var userInfo = uri.UserInfo.Split(':', 2);

        var builder = new NpgsqlConnectionStringBuilder
        {
            Host = uri.Host,
            Port = uri.Port > 0 ? uri.Port : 5432,
            Username = Uri.UnescapeDataString(userInfo[0]),
            Password = userInfo.Length > 1 ? Uri.UnescapeDataString(userInfo[1]) : null,
            Database = uri.AbsolutePath.TrimStart('/'),
            SslMode = SslMode.Prefer,
        };
        return builder.ConnectionString;
    }
}
