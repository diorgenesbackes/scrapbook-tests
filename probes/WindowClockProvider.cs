using System.Globalization;

// Somente o host de testes lê este arquivo privado; a API de produção não o referencia.
internal sealed class WindowClockProvider(string file) : TimeProvider
{
    public override DateTimeOffset GetUtcNow() => DateTimeOffset.Parse(File.ReadAllText(file), CultureInfo.InvariantCulture).ToUniversalTime();
}
