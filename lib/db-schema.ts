import { pgTable, text, vector, serial, timestamp, integer, jsonb } from "drizzle-orm/pg-core";

export const files = pgTable("files", {
  id: serial("id").primaryKey(),
  name: text("name").notNull(),
  type: text("type").notNull(),
  size: integer("size").notNull(),
  userId: text("user_id"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export const documents = pgTable(
  "documents",
  {
    id: serial("id").primaryKey(),
    fileId: integer("file_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    content: text("content").notNull(),
    metadata: jsonb("metadata"),
    embeddings: vector("embeddings", { dimensions: 3072 }).notNull(),
  }
);

export const chats = pgTable("chats", {
  id: text("id").primaryKey(),
  title: text("title").notNull(),
  userId: text("user_id"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export const chatMessages = pgTable("chat_messages", {
  id: text("id").primaryKey(),
  chatId: text("chat_id")
    .references(() => chats.id, { onDelete: "cascade" })
    .notNull(),
  role: text("role").notNull(),
  content: text("content").notNull(),
  parts: jsonb("parts"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export type InsertFile = typeof files.$inferInsert;
export type SelectFile = typeof files.$inferSelect;
export type InsertDocument = typeof documents.$inferInsert;
export type SelectDocument = typeof documents.$inferSelect;
export type InsertChat = typeof chats.$inferInsert;
export type SelectChat = typeof chats.$inferSelect;
export type InsertChatMessage = typeof chatMessages.$inferInsert;
export type SelectChatMessage = typeof chatMessages.$inferSelect;
