def detect_images(page):

    images = page.get_images(full=True)

    return images