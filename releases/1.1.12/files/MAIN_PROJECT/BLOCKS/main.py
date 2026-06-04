import pygame
import sys


pygame.init()


width, height = 700, 700
screen = pygame.display.set_mode((width, height))
pygame.display.set_caption("TeploScope")

image_path = "1.jpg"
image = pygame.image.load(image_path)
image = pygame.transform.scale(image, (600, 600))
image_rect = image.get_rect(center=(width // 2, height // 2))


angle = 0
rotation_speed = 60

clock = pygame.time.Clock()

while True:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

    screen.fill((255, 255, 255))

    angle -= rotation_speed
    if angle >= 360:
        angle -= 360

    rotated_image = pygame.transform.rotate(image, angle)
    rotated_rect = rotated_image.get_rect(center=image_rect.center)

    screen.blit(rotated_image, rotated_rect.topleft)

    pygame.display.flip()

    clock.tick(10)