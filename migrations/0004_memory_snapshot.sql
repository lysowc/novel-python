-- 小说记忆按章快照：每章写完后保存"第 N 章结束时"的记忆
-- 用于重新生成中间章节时取当时的记忆，而不是全书最新记忆

CREATE TABLE `novel_memory_snapshot` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `chapter_no` INT UNSIGNED NOT NULL,
  `content` LONGTEXT NULL,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_novel_chapter` (`novel_id`, `chapter_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
