using System.Net;
using System.Text;

namespace backend_dotnet.Tests;

// Stands in for the network call to Supabase's JWKS endpoint: always returns
// the fixed JSON it was built with, regardless of the request URL.
public sealed class StubHttpMessageHandler(string jsonBody) : HttpMessageHandler
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        var response = new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(jsonBody, Encoding.UTF8, "application/json"),
        };
        return Task.FromResult(response);
    }
}
