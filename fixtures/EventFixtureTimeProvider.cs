using System.Globalization;

internal sealed class EventFixtureTimeProvider : TimeProvider
{
    public override DateTimeOffset GetUtcNow()
    {
        try
        {
            return DateTimeOffset.Parse(File.ReadAllText("/test-clock/now"), CultureInfo.InvariantCulture);
        }
        catch (Exception error) when (error is IOException or FormatException)
        {
            Console.Error.WriteLine($"Relógio da fixture: {error.GetType().Name}");
            throw;
        }
    }
}
