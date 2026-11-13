using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;
using ScrapBook.Application.Modules.Events;
using ScrapBook.Application.Modules.Identity;
using ScrapBook.Domain.Common;
using ScrapBook.Domain.Modules.Events;
using ScrapBook.Infrastructure.Modules.Events;
using ScrapBook.Infrastructure.Modules.Events.Persistence;
using ScrapBook.Infrastructure.Modules.Identity;

internal static class DatabaseProbes
{
    public static async Task RunAsync()
    {
        var connection = Environment.GetEnvironmentVariable("ConnectionStrings__ScrapBook")
            ?? throw new InvalidOperationException("Conexão local não configurada.");
        var parsed = new NpgsqlConnectionStringBuilder(connection);
        if (parsed.Host is not ("127.0.0.1" or "localhost" or "::1"))
            throw new InvalidOperationException("Probe exige PostgreSQL em loopback.");
        var actorId = long.Parse(Environment.GetEnvironmentVariable("SCRAPBOOK_PROBE_ACCOUNT_ID") ?? "0");
        var services = new ServiceCollection().AddLogging(log => log.AddFilter(_ => false));
        services.AddEventsPersistence(connection).AddDevelopmentIdentity(connection, true, actorId);
        await using var provider = services.BuildServiceProvider();
        await using var scope = provider.CreateAsyncScope();
        await using var otherScope = provider.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<EventsDbContext>();
        Check(!ReferenceEquals(database, otherScope.ServiceProvider.GetRequiredService<EventsDbContext>()), "DbContext deve ser scoped.");
        Check(database.Model.GetEntityTypes().All(type => type.GetSchema() == "events"), "Contexto Events cruzou módulo.");
        Check(database.Model.GetEntityTypes().Count() == 4, "Inventário de mapeamentos inesperado.");
        var entity = database.Model.FindEntityType(typeof(EventRow)) ?? throw new InvalidOperationException();
        Check(entity.FindProperty(nameof(EventRow.Version))!.IsConcurrencyToken, "Version sem concorrência.");
        Check(entity.FindProperty(nameof(EventRow.Version))!.ValueGenerated == ValueGenerated.OnAddOrUpdate, "Version deve vir do banco.");
        var actor = await scope.ServiceProvider.GetRequiredService<ICurrentActor>().ResolveAsync(default);
        Check(actor?.AccountId == actorId, "Identity não validou fixture.");
        var baseline = await database.Events.CountAsync();
        await using (var transaction = await database.Database.BeginTransactionAsync())
        {
            var clock = new BusinessClock(TimeProvider.System);
            var name = string.Concat(Enumerable.Repeat("🎉", 120));
            var welcome = string.Concat(Enumerable.Repeat("🎉", 2000));
            var state = EventRules.Create(actorId, name, clock.CivilNow.Date.AddDays(30).AddHours(18),
                welcome, "scheduled", null, clock.CivilNow);
            var row = EventRow.FromSnapshot(state);
            database.Events.Add(row);
            await database.SaveChangesAsync();
            database.Members.Add(new EventMemberRow { EventId = row.Id, AccountId = actorId, Slot = 1, JoinedAt = clock.UtcNow });
            database.Audit.Add(new EventAuditRow { AccountId = actorId, EventId = row.Id,
                Action = "events.created", SubjectId = row.Id, CorrelationId = Guid.NewGuid() });
            await database.SaveChangesAsync();
            await database.Database.ExecuteSqlRawAsync("SET CONSTRAINTS ALL IMMEDIATE;");
            await database.Entry(row).ReloadAsync();
            Check(row.Name == name && row.WelcomeMessage == welcome, "Provider truncou escalares Unicode.");
            Check(row.Id > 0 && row.Version == 0 && row.AccessVersion == 1, "Valores gerados inválidos.");
            Check(await database.ForActor(actorId).AnyAsync(e => e.Id == row.Id), "Vínculo autorizado não encontrado.");
            Check(!await database.ForActor(long.MaxValue).AnyAsync(e => e.Id == row.Id), "Filtro ampliou acesso.");
            row.Apply(EventRules.UpdateAccess(row.Snapshot(), "immediate", null, clock.CivilNow).State);
            await database.SaveChangesAsync();
            await database.Entry(row).ReloadAsync();
            Check(row.Version == 1 && row.AccessVersion == 2 && row.OpeningMode == "immediate", "Trigger não retornou estado final.");
            Check(await database.SaveChangesAsync() == 0, "No-op gerou escrita.");
            await transaction.RollbackAsync();
        }

        database.ChangeTracker.Clear();
        Check(await database.Events.CountAsync() == baseline, "Rollback deixou evento parcial.");
        Console.WriteLine(JsonSerializer.Serialize(new { probe = "events_postgresql_mapping", passed = true,
            scoped = true, unicode120 = true, unicode2000 = true, actor_contract = true,
            membership_filter = true, trigger_versions = true, rollback = true }));
    }

    private static void Check(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
    }
}
