/* SPDX-License-Identifier: LGPL-2.0-or-later
 * Copyright (C) 2026 SpeedyCPU contributors
 *
 * This program is free software; you can redistribute it and/or modify it under
 * the terms of the GNU Library General Public License as published by the Free
 * Software Foundation; either version 2 of the License, or (at your option) any
 * later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
 * FOR A PARTICULAR PURPOSE.  See the GNU Library General Public License for more
 * details.  You should have received a copy of it in the LICENSE file.
 */

package dev.speedycpu.agent;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal JSON reader/writer.
 *
 * <p>The agent has to share {@code config.json} with the Python CLI and write
 * {@code agent.json} that the CLI can read back. A full JSON library would mean
 * shading a jar or depending on Maven Central at build time; for the handful of
 * types involved (string, number, boolean, null, array, object) a self
 * contained parser is smaller than the build-system glue it replaces.
 */
public final class Json {

    private Json() {}

    // ------------------------------------------------------------------
    // Parsing
    // ------------------------------------------------------------------
    public static Object parse(String text) {
        Parser parser = new Parser(text);
        parser.skipWhitespace();
        Object value = parser.parseValue();
        parser.skipWhitespace();
        return value;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        Object value = parse(text);
        return value instanceof Map ? (Map<String, Object>) value : new LinkedHashMap<>();
    }

    public static Map<String, Object> readObject(Path path) throws IOException {
        if (!Files.exists(path)) {
            return new LinkedHashMap<>();
        }
        return parseObject(Files.readString(path, StandardCharsets.UTF_8));
    }

    private static final class Parser {
        private final String text;
        private int index;

        Parser(String text) {
            this.text = text;
        }

        void skipWhitespace() {
            while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
                index++;
            }
        }

        Object parseValue() {
            skipWhitespace();
            if (index >= text.length()) {
                return null;
            }
            char c = text.charAt(index);
            switch (c) {
                case '{':
                    return parseObjectValue();
                case '[':
                    return parseArray();
                case '"':
                    return parseString();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return parseNumber();
            }
        }

        private void expect(String literal) {
            if (!text.startsWith(literal, index)) {
                throw new IllegalArgumentException("invalid JSON at " + index);
            }
            index += literal.length();
        }

        private Map<String, Object> parseObjectValue() {
            Map<String, Object> map = new LinkedHashMap<>();
            index++; // {
            skipWhitespace();
            if (index < text.length() && text.charAt(index) == '}') {
                index++;
                return map;
            }
            while (index < text.length()) {
                skipWhitespace();
                String key = parseString();
                skipWhitespace();
                if (index >= text.length() || text.charAt(index) != ':') {
                    throw new IllegalArgumentException("expected ':' at " + index);
                }
                index++;
                map.put(key, parseValue());
                skipWhitespace();
                if (index >= text.length()) {
                    break;
                }
                char next = text.charAt(index);
                if (next == ',') {
                    index++;
                    continue;
                }
                if (next == '}') {
                    index++;
                    break;
                }
                throw new IllegalArgumentException("unexpected '" + next + "' at " + index);
            }
            return map;
        }

        private List<Object> parseArray() {
            List<Object> list = new ArrayList<>();
            index++; // [
            skipWhitespace();
            if (index < text.length() && text.charAt(index) == ']') {
                index++;
                return list;
            }
            while (index < text.length()) {
                list.add(parseValue());
                skipWhitespace();
                if (index >= text.length()) {
                    break;
                }
                char next = text.charAt(index);
                if (next == ',') {
                    index++;
                    continue;
                }
                if (next == ']') {
                    index++;
                    break;
                }
                throw new IllegalArgumentException("unexpected '" + next + "' at " + index);
            }
            return list;
        }

        private String parseString() {
            if (index >= text.length() || text.charAt(index) != '"') {
                throw new IllegalArgumentException("expected a string at " + index);
            }
            index++;
            StringBuilder out = new StringBuilder();
            while (index < text.length()) {
                char c = text.charAt(index++);
                if (c == '"') {
                    break;
                }
                if (c != '\\') {
                    out.append(c);
                    continue;
                }
                if (index >= text.length()) {
                    break;
                }
                char escape = text.charAt(index++);
                switch (escape) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        if (index + 4 <= text.length()) {
                            out.append((char) Integer.parseInt(text.substring(index, index + 4), 16));
                            index += 4;
                        }
                    }
                    default -> out.append(escape);
                }
            }
            return out.toString();
        }

        private Object parseNumber() {
            int start = index;
            while (index < text.length()
                    && "+-0123456789.eE".indexOf(text.charAt(index)) >= 0) {
                index++;
            }
            String raw = text.substring(start, index);
            if (raw.isEmpty()) {
                throw new IllegalArgumentException("invalid JSON at " + start);
            }
            if (raw.indexOf('.') < 0 && raw.indexOf('e') < 0 && raw.indexOf('E') < 0) {
                try {
                    return Long.valueOf(raw);
                } catch (NumberFormatException ignored) {
                    // fall through to double
                }
            }
            return Double.valueOf(raw);
        }
    }

    // ------------------------------------------------------------------
    // Accessors with defaults
    // ------------------------------------------------------------------
    public static String string(Map<String, Object> map, String key, String fallback) {
        Object value = map.get(key);
        return value == null ? fallback : String.valueOf(value);
    }

    public static int integer(Map<String, Object> map, String key, int fallback) {
        Object value = map.get(key);
        if (value instanceof Number number) {
            return number.intValue();
        }
        return fallback;
    }

    public static boolean bool(Map<String, Object> map, String key, boolean fallback) {
        Object value = map.get(key);
        return value instanceof Boolean b ? b : fallback;
    }

    @SuppressWarnings("unchecked")
    public static List<String> stringList(Map<String, Object> map, String key) {
        Object value = map.get(key);
        List<String> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (item != null) {
                    out.add(String.valueOf(item));
                }
            }
        }
        return out;
    }

    // ------------------------------------------------------------------
    // Writing
    // ------------------------------------------------------------------
    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(out, value, 0);
        return out.toString();
    }

    private static void writeValue(StringBuilder out, Object value, int depth) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String text) {
            writeString(out, text);
        } else if (value instanceof Boolean || value instanceof Number) {
            out.append(value);
        } else if (value instanceof Map<?, ?> map) {
            writeMap(out, map, depth);
        } else if (value instanceof List<?> list) {
            writeList(out, list, depth);
        } else {
            writeString(out, String.valueOf(value));
        }
    }

    private static void writeMap(StringBuilder out, Map<?, ?> map, int depth) {
        if (map.isEmpty()) {
            out.append("{}");
            return;
        }
        out.append("{\n");
        int remaining = map.size();
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            indent(out, depth + 1);
            writeString(out, String.valueOf(entry.getKey()));
            out.append(": ");
            writeValue(out, entry.getValue(), depth + 1);
            out.append(--remaining > 0 ? ",\n" : "\n");
        }
        indent(out, depth);
        out.append('}');
    }

    private static void writeList(StringBuilder out, List<?> list, int depth) {
        if (list.isEmpty()) {
            out.append("[]");
            return;
        }
        out.append("[\n");
        for (int i = 0; i < list.size(); i++) {
            indent(out, depth + 1);
            writeValue(out, list.get(i), depth + 1);
            out.append(i == list.size() - 1 ? "\n" : ",\n");
        }
        indent(out, depth);
        out.append(']');
    }

    private static void indent(StringBuilder out, int depth) {
        out.append("  ".repeat(Math.max(0, depth)));
    }

    private static void writeString(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
                }
            }
        }
        out.append('"');
    }
}
