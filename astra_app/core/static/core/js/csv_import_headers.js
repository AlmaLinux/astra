(function () {
  function norm(value) {
    return (value || "").toString().trim().toLowerCase().replace(/[^a-z0-9]/g, "");
  }

  function chooseDelimiter(line) {
    const candidates = [",", "\t", ";", "|"];
    let best = ",";
    let bestCount = -1;
    for (const delimiter of candidates) {
      const count = line.split(delimiter).length - 1;
      if (count > bestCount) {
        bestCount = count;
        best = delimiter;
      }
    }
    return best;
  }

  function parseCsvRow(line, delimiter) {
    const out = [];
    let current = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuotes && line[i + 1] === '"') {
          current += '"';
          i += 1;
          continue;
        }
        inQuotes = !inQuotes;
        continue;
      }
      if (ch === delimiter && !inQuotes) {
        out.push(current.trim());
        current = "";
        continue;
      }
      current += ch;
    }
    if (current || line.endsWith(delimiter)) {
      out.push(current.trim());
    }
    return out.filter(Boolean);
  }

  function pickHeader(headers, preferredNorms) {
    const normalized = headers.map((header) => ({ raw: header, norm: norm(header) }));
    for (const preferred of preferredNorms) {
      const match = normalized.find((item) => item.norm === norm(preferred));
      if (match) {
        return match.raw;
      }
    }
    return "";
  }

  window.csvImportHeaders = {
    norm,
    chooseDelimiter,
    parseCsvRow,
    pickHeader,
  };
})();
