<?php
/** Insert one post into the existing winning_cases board (module 158). */
if (PHP_SAPI !== 'cli') {
	fwrite(STDERR, "CLI only\n");
	exit(1);
}

$jsonPath = $argv[1] ?? '';
$imageDir = $argv[2] ?? '';
if (!$jsonPath || !is_file($jsonPath)) {
	fwrite(STDERR, "usage: php _winning_case_importer.php post.json image_dir\n");
	exit(1);
}

chdir(__DIR__);
require __DIR__ . '/common/autoload.php';
Context::init();

$moduleSrl = 158;
$memberSrl = 587;
$member = MemberModel::getMemberInfoByMemberSrl($memberSrl);
if (!$member || !$member->member_srl) {
	fwrite(STDERR, "member not found\n");
	exit(2);
}
$member->is_admin = 'Y';
Context::set('logged_info', $member);
Context::set('is_logged', true);
$GLOBALS['__logged_info__'] = $member;

$post = json_decode(file_get_contents($jsonPath), true);
if (!is_array($post)) {
	fwrite(STDERR, "bad json\n");
	exit(3);
}

$title = trim((string)($post['title'] ?? ''));
$content = (string)($post['content'] ?? '');
$categorySrl = (int)($post['category_srl'] ?? 0);
$images = is_array($post['images'] ?? null) ? $post['images'] : [];
$representativeIndex = (int)($post['representative_index'] ?? 0);
$extraVars = is_array($post['extra_vars'] ?? null) ? $post['extra_vars'] : [];
if ($title === '' || !$categorySrl || !$images) {
	fwrite(STDERR, "missing required data\n");
	exit(4);
}

$documentSrl = getNextSequence();
$documentController = DocumentController::getInstance();
$fileController = FileController::getInstance();
$fileSrls = [];
$imageHtml = [];

foreach ($images as $index => $image) {
	$name = basename((string)($image['name'] ?? ''));
	$sourceName = trim((string)($image['source_name'] ?? $name));
	$path = rtrim($imageDir, "/\\") . DIRECTORY_SEPARATOR . $name;
	if (!$name || !is_file($path)) {
		fwrite(STDERR, "image missing: {$name}\n");
		exit(5);
	}
	$fileInfo = [
		'name' => $sourceName ?: $name,
		'tmp_name' => $path,
		'size' => filesize($path),
		'error' => 0,
	];
	$output = $fileController->insertFile($fileInfo, $moduleSrl, $documentSrl, 0, true);
	if (!$output->toBool()) {
		fwrite(STDERR, "image upload failed: " . $output->getMessage() . "\n");
		exit(6);
	}
	$fileSrl = (int)$output->get('file_srl');
	$fileSrls[] = $fileSrl;
	$file = FileModel::getFile($fileSrl);
	if ($file && !empty($file->uploaded_filename)) {
		$relative = preg_replace('@^\./@', '', str_replace('\\', '/', $file->uploaded_filename));
		$url = '/' . ltrim($relative, '/');
		$imageHtml[] = '<p><img src="' . htmlspecialchars($url, ENT_QUOTES, 'UTF-8')
			. '" alt="' . htmlspecialchars($sourceName ?: $title, ENT_QUOTES, 'UTF-8')
			. '" editor_component="image_link" data-file-srl="' . $fileSrl . '" /></p>';
	}
}

if ($representativeIndex < 0 || $representativeIndex >= count($fileSrls)) {
	$representativeIndex = 0;
}
$representativeSrl = $fileSrls[$representativeIndex];
$thumbnailHtml = [$imageHtml[$representativeIndex]];
foreach ($imageHtml as $index => $markup) {
	if ($index !== $representativeIndex) {
		$thumbnailHtml[] = $markup;
	}
}
$content = implode("\n", $thumbnailHtml) . "\n" . $content;

$object = new stdClass();
$object->document_srl = $documentSrl;
$object->module_srl = $moduleSrl;
$object->category_srl = $categorySrl;
$object->member_srl = $memberSrl;
$object->user_id = $member->user_id;
$object->user_name = $member->user_name ?: $member->nick_name;
$object->nick_name = $member->nick_name;
$object->email_address = isset($member->email_address) ? $member->email_address : '';
$object->title = $title;
$object->content = $content;
$object->tags = trim((string)($post['tags'] ?? ''));
$object->status = 'PUBLIC';
$object->comment_status = 'DENY';
$object->allow_trackback = 'N';
$object->notify_message = 'N';
$object->is_notice = 'N';
$object->uploaded_count = count($fileSrls);

$eidByIndex = [
	1 => 'lawyer',
	2 => 'case_background',
	3 => 'lawyer_support',
	4 => 'case_result',
	5 => 'case_significance',
	6 => 'case_title',
	7 => 'case_tag',
	8 => 'case_img',
	9 => 'case_keyword',
];
$extraVars['case_img'] = (string)$representativeSrl;
foreach ($eidByIndex as $index => $eid) {
	$object->{'extra_vars' . $index} = (string)($extraVars[$eid] ?? '');
}

$output = $documentController->insertDocument($object, true);
if (!$output->toBool()) {
	fwrite(STDERR, "document insert failed: " . $output->getMessage() . "\n");
	exit(7);
}
foreach ($eidByIndex as $index => $eid) {
	DocumentController::insertDocumentExtraVar(
		$moduleSrl,
		$documentSrl,
		$index,
		(string)($extraVars[$eid] ?? ''),
		$eid
	);
}
$fileController->setFilesValid($documentSrl, 'doc');

echo "OK {$documentSrl} {$title}\n";
echo "FILES " . implode(',', $fileSrls) . "\n";
