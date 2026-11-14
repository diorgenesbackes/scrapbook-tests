using System.Diagnostics;
using System.Diagnostics.Metrics;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;
using ScrapBook.Application.Interfaces;
using ScrapBook.Application.Services;
using ScrapBook.Domain.Clocks;
using ScrapBook.Domain.Models;
using ScrapBook.Infrastructure.DbContexts;
using ScrapBook.Infrastructure.Extensions;

internal static class PublicPrivacyProbe
{
    public static async Task RunAsync()
    {
        var connection = Environment.GetEnvironmentVariable("ConnectionStrings__ScrapBook") ?? throw new InvalidOperationException();
        var parsed = new NpgsqlConnectionStringBuilder(connection);
        if (parsed.Host != "127.0.0.1" || parsed.Database != "scrapbook_docker_test") throw new InvalidOperationException("Exige laboratório local.");
        var code = Environment.GetEnvironmentVariable("SCRAPBOOK_PRIVACY_CODE") ?? throw new InvalidOperationException();
        var capture = new PublicQueryCommandInterceptor();
        var telemetry = new EventTelemetryService("Testing", "privacy-probe", 1);
        var spans = new List<object>();
        using var listener = new ActivityListener
        {
            ShouldListenTo = source => ReferenceEquals(source, telemetry.Source),
            Sample = (ref ActivityCreationOptions<ActivityContext> _) => ActivitySamplingResult.AllDataAndRecorded,
            ActivityStopped = activity => spans.Add(new { operation = activity.OperationName, tags = activity.TagObjects.ToDictionary(x => x.Key, x => x.Value) })
        };
        ActivitySource.AddActivityListener(listener);
        var measurements = new List<object>();
        using var meter = new MeterListener();
        meter.InstrumentPublished = (instrument, owner) =>
        {
            if (instrument.Meter.Name == EventTelemetryService.MeterName) owner.EnableMeasurementEvents(instrument);
        };
        meter.SetMeasurementEventCallback<double>((instrument, _, tags, _) => measurements.Add(new { instrument = instrument.Name, tags = tags.ToArray().ToDictionary(x => x.Key, x => x.Value) }));
        meter.Start();
        var services = new ServiceCollection().AddLogging(log => log.AddFilter(_ => false));
        services.AddSingleton(telemetry);
        services.AddPersistence(connection);
        services.AddDbContext<ScrapBookDbContext>(options => options.AddInterceptors(capture));
        await using var provider = services.BuildServiceProvider();
        await using var scope = provider.CreateAsyncScope();
        var repository = scope.ServiceProvider.GetRequiredService<IPublicEventRepository>();
        var service = new PublicEventService(repository, new PublicSiteModel(null), new BusinessClock(TimeProvider.System));
        var value = await service.GetAsync(code, default);
        if (value.Code != code || capture.Commands.Count != 1) throw new InvalidOperationException("Consulta inesperada.");
        var sql = capture.Commands.Single();
        var columns = Regex.Matches(sql.Split("FROM")[0], @"e\.(\w+)").Select(x => x.Groups[1].Value).Order().ToArray();
        if (!columns.SequenceEqual(new[] { "code", "name", "starts_at", "welcome_message" }) || !sql.Contains("FROM events.events AS e")
            || sql.Contains("JOIN") || sql.Contains("records.") || sql.Contains("identity.") || sql.Contains(code))
            throw new InvalidOperationException("Projeção pública ampliada.");
        var signals = JsonSerializer.Serialize(new { spans, measurements });
        if (spans.Count != 1 || measurements.Count != 1 || signals.Contains(code) || signals.Contains("PRIVATE_GUEST") || signals.Contains("http.url"))
            throw new InvalidOperationException("Sinais não sanitizados.");
        Console.WriteLine(JsonSerializer.Serialize(new { probe = "public_privacy", passed = true, sql, columns, spans, measurements,
            automatic_exporters = false, remote_tracing_validated = false }));
    }
}
