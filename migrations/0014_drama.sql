-- 短剧：从已写章节改编成分集方案、剧本、分镜

CREATE TABLE `drama_episode` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `novel_id` INT UNSIGNED NOT NULL,
  `episode_no` INT UNSIGNED NOT NULL,
  `title` VARCHAR(255) NOT NULL DEFAULT '',
  `chapter_from` INT UNSIGNED NOT NULL DEFAULT 0,
  `chapter_to` INT UNSIGNED NOT NULL DEFAULT 0,
  `summary` TEXT NULL,
  `hook` TEXT NULL,
  `cliffhanger` TEXT NULL,
  `script` LONGTEXT NULL,
  `script_at` DATETIME NULL,
  `storyboard` LONGTEXT NULL,
  `storyboard_at` DATETIME NULL,
  `created_at` DATETIME NULL,
  `updated_at` DATETIME NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_novel_episode` (`novel_id`, `episode_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
