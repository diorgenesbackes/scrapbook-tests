using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;
using ScrapBook.Api.Controllers;
using ScrapBook.Api.Middlewares;
using ScrapBook.Api.Options;
using ScrapBook.Application.Interfaces;
using ScrapBook.Application.Services;
using ScrapBook.Domain.Clocks;
using ScrapBook.Domain.Models;
using ScrapBook.Infrastructure.Extensions;

internal static class PublicWindowHttpProbe
{
    public static async Task RunAsync()
    {
        var connection = Environment.GetEnvironmentVariable("ConnectionStrings__ScrapBook") ?? throw new InvalidOperationException();
        var parsed = new NpgsqlConnectionStringBuilder(connection);
        var target = new Uri(Environment.GetEnvironmentVariable("SCRAPBOOK_WINDOW_HOST") ?? "");
        if (parsed.Host is not ("127.0.0.1" or "localhost" or "::1") || target.Scheme != "http" || !target.IsLoopback)
            throw new InvalidOperationException("Probe temporal exige destinos loopback.");
        var file = Environment.GetEnvironmentVariable("SCRAPBOOK_WINDOW_CLOCK") ?? throw new InvalidOperationException();
        if (!File.Exists(file) || new FileInfo(file).LinkTarget is not null) throw new InvalidOperationException("Relógio de teste inválido.");
        var builder = WebApplication.CreateBuilder(new WebApplicationOptions { EnvironmentName = "Testing" });
        builder.WebHost.UseUrls(target.GetLeftPart(UriPartial.Authority));
        builder.Logging.ClearProviders();
        builder.Services.AddHealthChecks();
        builder.Services.AddControllers().AddApplicationPart(typeof(PublicEventController).Assembly);
        builder.Services.AddSingleton<TimeProvider>(new WindowClockProvider(file));
        builder.Services.AddSingleton<BusinessClock>();
        builder.Services.AddSingleton(new PublicSiteModel(Environment.GetEnvironmentVariable("SCRAPBOOK_PUBLIC_ORIGIN")));
        builder.Services.AddPersistence(connection).AddDevelopmentIdentity(false, 0);
        builder.Services.AddSingleton(new DevelopmentIdentityOptions());
        builder.Services.AddScoped<IPublicEventService, PublicEventService>();
        await using var app = builder.Build();
        app.UseRouting();
        app.UseMiddleware<EventRequestMiddleware>();
        app.MapControllers();
        await app.RunAsync();
    }
}
