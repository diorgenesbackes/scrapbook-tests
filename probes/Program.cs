using ScrapBook.Api.Extensions;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Hosting;

if (args.FirstOrDefault() == "models")
{
    try { await DatabaseProbes.RunAsync(); }
    catch (Exception error)
    {
        Console.Error.WriteLine(System.Text.Json.JsonSerializer.Serialize(new
        {
            probe = "events_postgresql_mapping", passed = false, error = error.GetType().Name,
            sqlState = (error.InnerException as Npgsql.PostgresException)?.SqlState
        }));
        Environment.ExitCode = 1;
    }
    return;
}

var root = Path.Combine(Path.GetTempPath(), "scrapbook-probe-" + Guid.NewGuid().ToString("N"));
try
{
    var configuration = new ConfigurationBuilder().AddInMemoryCollection(new Dictionary<string, string?>
    {
        ["Events:CursorKeyPath"] = root
    }).Build();
    ServiceProvider Provider()
    {
        var services = new ServiceCollection().AddLogging();
        services.AddCursorDataProtection(configuration, new ProbeEnvironment());
        return services.BuildServiceProvider();
    }

    string token;
    using (var first = Provider())
    {
        token = first.GetRequiredService<IDataProtectionProvider>()
            .CreateProtector("Events.List.v1").Protect("synthetic-private-cursor");
    }

    using (var restarted = Provider())
    {
        var value = restarted.GetRequiredService<IDataProtectionProvider>()
            .CreateProtector("Events.List.v1").Unprotect(token);
        if (value != "synthetic-private-cursor") throw new InvalidOperationException("Cursor inválido após recriar provider.");
    }

    if (!OperatingSystem.IsWindows() && File.GetUnixFileMode(root) !=
        (UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute))
    {
        throw new InvalidOperationException("Permissão do diretório de chaves não é privada.");
    }

    Console.WriteLine("{\"probe\":\"cursor_keys_restart\",\"passed\":true}");
}
finally
{
    if (Directory.Exists(root)) Directory.Delete(root, true);
}

sealed class ProbeEnvironment : IHostEnvironment
{
    public string EnvironmentName { get; set; } = "Development";
    public string ApplicationName { get; set; } = "ScrapBook.Probes";
    public string ContentRootPath { get; set; } = Directory.GetCurrentDirectory();
    public IFileProvider ContentRootFileProvider { get; set; } = new NullFileProvider();
}
