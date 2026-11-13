using ScrapBook.Infrastructure.Extensions;
using ScrapBook.Application.Interfaces;
using ScrapBook.Domain.Clocks;
using ScrapBook.Domain.Policies;
using ScrapBook.Infrastructure.DbContexts;
using ScrapBook.Domain.Entities;
using System.Text.Json;
using Microsoft.EntityFrameworkCore;
using Microsoft.EntityFrameworkCore.Metadata;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;

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
        services.AddPersistence(connection).AddDevelopmentIdentity(true, actorId);
        await using var provider = services.BuildServiceProvider();
        await using var scope = provider.CreateAsyncScope();
        await using var otherScope = provider.CreateAsyncScope();
        var database = scope.ServiceProvider.GetRequiredService<ScrapBookDbContext>();
        Check(!ReferenceEquals(database, otherScope.ServiceProvider.GetRequiredService<ScrapBookDbContext>()), "DbContext deve ser scoped.");
        Check(database.Model.GetEntityTypes().All(type => type.GetSchema() is "events" or "identity"), "Schema de persistência inesperado.");
        Check(database.Model.GetEntityTypes().Count() == 5, "Inventário de mapeamentos inesperado.");
        var entity = database.Model.FindEntityType(typeof(EventPersistenceEntity)) ?? throw new InvalidOperationException();
        Check(entity.FindProperty(nameof(EventPersistenceEntity.Version))!.IsConcurrencyToken, "Version sem concorrência.");
        Check(entity.FindProperty(nameof(EventPersistenceEntity.Version))!.ValueGenerated == ValueGenerated.OnAddOrUpdate, "Version deve vir do banco.");
        var actor = await scope.ServiceProvider.GetRequiredService<ICurrentActorService>().ResolveAsync(default);
        Check(actor?.AccountId == actorId, "Identity não validou fixture.");
        var repository = scope.ServiceProvider.GetRequiredService<IEventRepository>();
        var baseline = await database.Events.CountAsync();
        await using (var transaction = await database.Database.BeginTransactionAsync())
        {
            var clock = new BusinessClock(TimeProvider.System);
            var name = string.Concat(Enumerable.Repeat("🎉", 120));
            var welcome = string.Concat(Enumerable.Repeat("🎉", 2000));
            var state = EventPolicy.Create(actorId, name, clock.CivilNow.Date.AddDays(30).AddHours(18),
                welcome, "scheduled", null, clock.CivilNow);
            var row = EventPersistenceEntity.FromSnapshot(state);
            database.Events.Add(row);
            await database.SaveChangesAsync();
            database.Members.Add(new EventMemberPersistenceEntity { EventId = row.Id, AccountId = actorId, Slot = 1, JoinedAt = clock.UtcNow });
            database.Audit.Add(new EventAuditPersistenceEntity { AccountId = actorId, EventId = row.Id,
                Action = "events.created", SubjectId = row.Id, CorrelationId = Guid.NewGuid() });
            await database.SaveChangesAsync();
            await database.Database.ExecuteSqlRawAsync("SET CONSTRAINTS ALL IMMEDIATE;");
            await database.Entry(row).ReloadAsync();
            Check(row.Name == name && row.WelcomeMessage == welcome, "Provider truncou escalares Unicode.");
            Check(row.Id > 0 && row.Version == 0 && row.AccessVersion == 1, "Valores gerados inválidos.");
            Check(await repository.GetAsync(actorId, row.Id, default) is not null, "Vínculo autorizado não encontrado.");
            Check(await repository.GetAsync(long.MaxValue, row.Id, default) is null, "Filtro ampliou acesso.");
            row.Apply(EventPolicy.UpdateAccess(row.Snapshot(), "immediate", null, clock.CivilNow).State);
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
